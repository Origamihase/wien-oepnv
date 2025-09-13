#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Builds a single RSS 2.0 feed for active ÖPNV-Beeinträchtigungen im Großraum Wien.
- Primäre Quelle: Wiener Linien (OGD Realtime) via providers.wiener_linien
- ÖBB/VOR sind vorbereitet (Provider liefern leer, bis Credentials/Implementierung vorhanden)
- TV-tauglich: Beschreibung wird zu kurzem Klartext ohne HTML/IMGs gekürzt
- Stabil: pubDate-Fallback vermeidet 'Jitter' durch Build-Zeit
- Altersfilter: MAX_ITEM_AGE_DAYS entfernt nur, wenn Item alt UND nicht mehr aktiv
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
MAX_ITEMS  = int(os.getenv("MAX_ITEMS", "60"))  # schlank & performant
LOG_LEVEL  = os.getenv("LOG_LEVEL",  "INFO")

# TV/Signage-Einstellungen
DESCRIPTION_CHAR_LIMIT = int(os.getenv("DESCRIPTION_CHAR_LIMIT", "170"))
FRESH_PUBDATE_WINDOW_MIN = int(os.getenv("FRESH_PUBDATE_WINDOW_MIN", "5"))

# Altersfilter (0 = aus). Achtung: nur "alt UND inaktiv" wird entfernt.
MAX_ITEM_AGE_DAYS = int(os.getenv("MAX_ITEM_AGE_DAYS", "0"))
ACTIVE_GRACE_MIN = int(os.getenv("ACTIVE_GRACE_MIN", "10"))  # Konsistenz mit Provider

VIENNA_TZ = ZoneInfo("Europe/Vienna")


# --------------------------------- Logging-Setup ---------------------------------

logging.basicConfig(level=LOG_LEVEL)
log = logging.getLogger("build_feed")


# --------------------------------- Hilfsfunktionen ---------------------------------

def _to_plain_for_signage(s: str, limit: int = DESCRIPTION_CHAR_LIMIT) -> str:
    """Klartext für TV – doppelt unescapen, HTML/IMG raus, NBSP→Space, kompakt."""
    if not s:
        return ""
    s = html.unescape(html.unescape(s))
    s = re.sub(r"<img\b[^>]*>", " ", s, flags=re.I)
    s = re.sub(r"</?(p|br|li|ul|ol|h\d)[^>]*>", " · ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("\u00A0", " ")
    s = re.sub(r"Linien:\s*\[([^\]]+)\]",
               lambda m: "Linien: " + ", ".join(t.strip().strip("'\"") for t in m.group(1).split(",")),
               s)
    s = re.sub(r"\bStops:\s*\[[^\]]*\]", " ", s)
    s = re.sub(r"\bBetroffene Haltestellen:\s*[0-9, …]+", " ", s)
    s = re.sub(r"\s{2,}", " ", s)
    s = re.sub(r"(?:\s*·\s*){2,}", " · ", s).strip()
    s = s.strip("· ,;:-")
    if len(s) > limit:
        s = s[:limit - 1].rstrip() + "…"
    return s


def _fmt_date(dt: datetime) -> str:
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
    SubElement(ch, "ttl").text = "15"
    SubElement(ch, "generator").text = "wien-oepnv (GitHub Actions)"
    return rss, ch


def _stable_pubdate_fallback(guid: str, now_local: datetime) -> datetime:
    base = now_local.replace(hour=6, minute=0, second=0, microsecond=0)
    h = int(hashlib.md5(guid.encode("utf-8")).hexdigest()[:8], 16)
    offset_sec = h % 3600
    return base + timedelta(seconds=offset_sec)


def _normalize_pubdate(ev: Dict[str, Any], build_now_local: datetime) -> datetime:
    dt = ev.get("pubDate")
    if not isinstance(dt, datetime):
        return _stable_pubdate_fallback(ev["guid"], build_now_local)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    window = timedelta(minutes=FRESH_PUBDATE_WINDOW_MIN)
    if (build_now_local.astimezone(timezone.utc) - dt) < window:
        return _stable_pubdate_fallback(ev["guid"], build_now_local)
    return dt


def _is_still_active(ev: Dict[str, Any], now_local: datetime) -> bool:
    """Aktiv, falls ends_at fehlt (offen) ODER in der Zukunft (mit Gnadenzeit)."""
    end = ev.get("ends_at")
    if not isinstance(end, datetime):
        return True  # kein Enddatum => als fortdauernd betrachten
    # Zeiten ohne TZ als UTC interpretieren
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return end >= (now_local - timedelta(minutes=ACTIVE_GRACE_MIN)).astimezone(end.tzinfo)


def _apply_age_filter(items: List[Dict[str, Any]], build_now_local: datetime) -> List[Dict[str, Any]]:
    """Entfernt nur Items, die (a) älter als MAX_ITEM_AGE_DAYS sind UND (b) nicht mehr aktiv."""
    if MAX_ITEM_AGE_DAYS <= 0:
        return items
    threshold_local = build_now_local - timedelta(days=MAX_ITEM_AGE_DAYS)
    kept = []
    for ev in items:
        pd = ev.get("pubDate")
        if not isinstance(pd, datetime):
            kept.append(ev)  # ohne Datum nicht hart filtern
            continue
        # für Vergleich in lokale TZ
        if pd.tzinfo is None:
            pd = pd.replace(tzinfo=timezone.utc)
        pd_local = pd.astimezone(VIENNA_TZ)
        if pd_local < threshold_local and not _is_still_active(ev, build_now_local):
            # alt und inaktiv => ausblenden
            continue
        kept.append(ev)
    return kept


def _add_item(ch, ev: Dict[str, Any], build_now_local: datetime) -> None:
    it = SubElement(ch, "item")

    # Titel NICHT un-escapen (damit '<' sicher bleibt)
    title = str(ev["title"]).strip()
    SubElement(it, "title").text = f"[{ev['source']}/{ev['category']}] {title}"

    # TV ohne Interaktion -> neutraler Link
    SubElement(it, "link").text = FEED_LINK

    short = _to_plain_for_signage(ev.get("description") or "")
    SubElement(it, "description").text = short or title

    stable_dt = _normalize_pubdate(ev, build_now_local)
    SubElement(it, "pubDate").text = _fmt_date(stable_dt)

    SubElement(it, "guid").text = ev["guid"]

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
                # Basisschema prüfen
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

    # Altersfilter anwenden (bewahrt aktive Langläufer wie U5-Bau)
    build_now_local = datetime.now(VIENNA_TZ)
    all_events = _apply_age_filter(all_events, build_now_local)

    # Sortieren & deckeln
    all_events.sort(key=lambda x: x["pubDate"], reverse=True)
    if MAX_ITEMS > 0 and len(all_events) > MAX_ITEMS:
        all_events = all_events[:MAX_ITEMS]

    # RSS bauen
    rss, ch = _rss_root(FEED_TITLE, FEED_LINK, FEED_DESC)
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
