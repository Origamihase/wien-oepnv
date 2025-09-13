#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Builds a single RSS 2.0 feed for active ÖPNV-Beeinträchtigungen im Großraum Wien.
- Primäre Quelle: Wiener Linien (OGD Realtime) via providers.wiener_linien
- ÖBB/VOR sind vorbereitet (Provider liefern leer, bis Credentials/Implementierung vorhanden)
- TV-tauglich: Beschreibung wird zu kurzem Klartext ohne HTML/IMGs gekürzt
- Stabil: pubDate-Fallback vermeidet 'Jitter' durch Build-Zeit
- Dedupe über GUID quer über alle Provider
"""

from __future__ import annotations

import os
import sys
import re
import html
import logging
import hashlib
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from xml.etree.ElementTree import Element, SubElement, tostring
from zoneinfo import ZoneInfo
from typing import List, Dict, Any

from providers import wiener_linien, oebb, vor


# ------------------------- Konfiguration (per ENV überschreibbar) -------------------------

FEED_TITLE = os.getenv("FEED_TITLE", "ÖPNV Störungen Wien & Umgebung")
FEED_LINK  = os.getenv("FEED_LINK",  "https://github.com/Origamihase/wien-oepnv")
FEED_DESC  = os.getenv("FEED_DESC",  "Aktive Störungen/Baustellen/Einschränkungen aus offiziellen Quellen")
OUT_PATH   = os.getenv("OUT_PATH",   "docs/feed.xml")
MAX_ITEMS  = int(os.getenv("MAX_ITEMS", "200"))
LOG_LEVEL  = os.getenv("LOG_LEVEL",  "INFO")

# TV/Signage-Einstellungen
DESCRIPTION_CHAR_LIMIT = int(os.getenv("DESCRIPTION_CHAR_LIMIT", "170"))   # Zeichenlimit für Klartext
FRESH_PUBDATE_WINDOW_MIN = int(os.getenv("FRESH_PUBDATE_WINDOW_MIN", "5")) # 'zu frisch' = innerhalb der letzten X Minuten

VIENNA_TZ = ZoneInfo("Europe/Vienna")


# --------------------------------- Logging-Setup ---------------------------------

logging.basicConfig(level=LOG_LEVEL)
log = logging.getLogger("build_feed")


# --------------------------------- Hilfsfunktionen ---------------------------------

def _to_plain_for_signage(s: str, limit: int = DESCRIPTION_CHAR_LIMIT) -> str:
    """
    Macht aus evtl. reichhaltigem HTML einen kurzen, gut lesbaren TV-Text.
    Reihenfolge ist wichtig:
      1) Entities lösen (html.unescape) — zweimal (gegen doppelt encodete Entities)
      2) <img> entfernen
      3) Block-Tags (p/br/li/ul/ol/h*) zu " · " umwandeln
      4) übrige HTML-Tags entfernen
      5) NBSP zu Leerzeichen
      6) WL-Listen ("Linien: [...]") verschönern
      7) Stops/ID-Listen entfernen
      8) Whitespace normalisieren, End-Trenner abwerfen, Kürzen
    """
    if not s:
        return ""

    # 1) Entities zuerst lösen – zweifach, um &amp;szlig; -> &szlig; -> ß abzudecken
    s = html.unescape(s)
    s = html.unescape(s)

    # 2) Bilder hart entfernen
    s = re.sub(r"<img\b[^>]*>", " ", s, flags=re.I)

    # 3) Semantische Umbrüche zu " · "
    s = re.sub(r"</?(p|br|li|ul|ol|h\d)[^>]*>", " · ", s, flags=re.I)

    # 4) Restliches HTML strippen
    s = re.sub(r"<[^>]+>", " ", s)

    # 5) NBSP zu normalem Leerzeichen
    s = s.replace("\u00A0", " ")

    # 6) "Linien: ['U6','U4']" -> "Linien: U6, U4"
    s = re.sub(
        r"Linien:\s*\[([^\]]+)\]",
        lambda m: "Linien: " + ", ".join(t.strip().strip("'\"") for t in m.group(1).split(",")),
        s,
    )

    # 7) Stops/ID-Listen am TV weglassen (nicht klickbar, wenig Mehrwert)
    s = re.sub(r"\bStops:\s*\[[^\]]*\]", " ", s)
    s = re.sub(r"\bBetroffene Haltestellen:\s*[0-9, …]+", " ", s)

    # 8) Whitespace, Trenner & Kürzung
    s = re.sub(r"\s{2,}", " ", s)
    s = re.sub(r"(?:\s*·\s*){2,}", " · ", s).strip()
    s = s.strip("· ,;:-")
    if len(s) > limit:
        s = s[:limit - 1].rstrip() + "…"
    return s


def _fmt_date(dt: datetime) -> str:
    """RFC 2822 Datum für RSS, immer in Europe/Vienna ausgeben."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return format_datetime(dt.astimezone(VIENNA_TZ))


def _rss_root(title: str, link: str, description: str):
    rss = Element("rss", version="2.0")
    ch  = SubElement(rss, "channel")
    SubElement(ch, "title").text = title
    SubElement(ch, "link").text = link
    SubElement(ch, "description").text = description
    SubElement(ch, "language").text = "de-AT"
    SubElement(ch, "lastBuildDate").text = _fmt_date(datetime.now(timezone.utc))
    SubElement(ch, "ttl").text = "15"  # Reader-Hinweis: alle 15 Min aktualisieren
    SubElement(ch, "generator").text = "wien-oepnv (GitHub Actions)"
    return rss, ch


def _stable_pubdate_fallback(guid: str, now_local: datetime) -> datetime:
    """
    Deterministischer, tagesstabiler Fallback für pubDate:
    - Basis: Heute 06:00 Europe/Vienna
    - Offset: Hash aus GUID (Sekunden innerhalb der ersten Stunde)
    So bleibt die Reihenfolge stabil ohne bei jedem Build 'neu' zu wirken.
    """
    base = now_local.replace(hour=6, minute=0, second=0, microsecond=0)
    h = int(hashlib.md5(guid.encode("utf-8")).hexdigest()[:8], 16)
    offset_sec = h % 3600  # innerhalb der ersten Stunde
    return base + timedelta(seconds=offset_sec)


def _normalize_pubdate(ev: Dict[str, Any], build_now_local: datetime) -> datetime:
    """
    Verwendet den vom Provider gelieferten pubDate.
    Falls der 'zu frisch' ist (innerhalb FRESH_PUBDATE_WINDOW_MIN vor Build-Zeit),
    setzen wir einen stabilen Fallback, um Jitter zu vermeiden.
    """
    dt = ev.get("pubDate")
    if not isinstance(dt, datetime):
        return _stable_pubdate_fallback(ev["guid"], build_now_local)

    # Wenn dt ohne TZ kommt -> als UTC interpretieren, dann in Wien ausgeben
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    window = timedelta(minutes=FRESH_PUBDATE_WINDOW_MIN)
    if (build_now_local.astimezone(timezone.utc) - dt) < window:
        return _stable_pubdate_fallback(ev["guid"], build_now_local)
    return dt


def _add_item(ch, ev: Dict[str, Any], build_now_local: datetime) -> None:
    """
    Erwartet ev mit Schlüsseln:
    source, category, title, description, link, guid, pubDate
    """
    it = SubElement(ch, "item")

    # Titel NICHT un-escapen (damit '<' sicher bleibt, s. St. Marx)
    title = str(ev["title"]).strip()
    SubElement(it, "title").text = f"[{ev['source']}/{ev['category']}] {title}"

    # Auf TVs kann man nicht klicken -> neutrales Link-Target (Channel-Link)
    SubElement(it, "link").text = FEED_LINK

    # TV-Kurztext (HTML entfernen/kürzen). Fallback auf Titel.
    short = _to_plain_for_signage(ev.get("description") or "")
    SubElement(it, "description").text = short or title

    # Stabilisiertes pubDate
    stable_dt = _normalize_pubdate(ev, build_now_local)
    SubElement(it, "pubDate").text = _fmt_date(stable_dt)

    # GUID beibehalten (Reader verwenden diese zur Dupe-Erkennung)
    SubElement(it, "guid").text = ev["guid"]

    # Kategorien helfen beim Filtern (auch wenn TV es ignoriert)
    for c in (ev.get("source"), ev.get("category")):
        if c:
            SubElement(it, "category").text = c


def _write_xml(elem, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = b'<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(elem, encoding="utf-8")
    with open(path, "wb") as f:
        f.write(data)
    log.info("Feed geschrieben: %s", path)


# -------------------------------------- Main --------------------------------------

def main() -> None:
    providers = (wiener_linien, oebb, vor)
    all_events: List[Dict[str, Any]] = []
    seen_guids: set[str] = set()

    for p in providers:
        try:
            events = p.fetch_events()
            cleaned: List[Dict[str, Any]] = []
            for ev in events:
                if not {"source","category","title","description","link","guid","pubDate"} <= ev.keys():
                    continue
                if not ev.get("guid"):
                    continue
                if ev["guid"] in seen_guids:
                    continue
                seen_guids.add(ev["guid"])
                cleaned.append(ev)
            all_events.extend(cleaned)
            log.info("%s lieferte %d Items", p.__name__, len(cleaned))
        except Exception as e:
            log.exception("Provider-Fehler bei %s: %s", p.__name__, e)

    all_events.sort(key=lambda x: x["pubDate"], reverse=True)
    if MAX_ITEMS > 0 and len(all_events) > MAX_ITEMS:
        all_events = all_events[:MAX_ITEMS]

    rss, ch = _rss_root(FEED_TITLE, FEED_LINK, FEED_DESC)
    build_now_local = datetime.now(VIENNA_TZ)

    for ev in all_events:
        _add_item(ch, ev, build_now_local)

    _write_xml(rss, OUT_PATH)
    log.info("Fertig: %d Items im Feed", len(all_events))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logging.exception("Abbruch: %s", e)
        sys.exit(1)
