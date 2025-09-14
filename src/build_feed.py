#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Builds a single RSS 2.0 feed für aktive ÖPNV-Beeinträchtigungen im Großraum Wien.
- Quellen: Wiener Linien (providers.wiener_linien), optional ÖBB/VOR (providers.oebb / providers.vor)
- TV-tauglich: Beschreibung -> kompakter Klartext, keine Bilder/HTML, Kürzung an Wortgrenzen
- Stabil: pubDate-Fallback (nie in der Zukunft gegenüber Build-Zeit)
- Altersfilter:
    * Behalte befristete Langläufer (Ende in der Zukunft)
    * Entferne unbefristete Items nach MAX_ITEM_AGE_DAYS / ABSOLUTE_MAX_AGE_DAYS
- Dedupe: GUID-weit eindeutig
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

# ------------------------- Konfiguration (per ENV überschreibbar) -------------------------

FEED_TITLE = os.getenv("FEED_TITLE", "ÖPNV Störungen Wien & Umgebung")
FEED_LINK  = os.getenv("FEED_LINK",  "https://github.com/Origamihase/wien-oepnv")
FEED_DESC  = os.getenv("FEED_DESC",  "Aktive Störungen/Baustellen/Einschränkungen aus offiziellen Quellen")
OUT_PATH   = os.getenv("OUT_PATH",   "docs/feed.xml")
MAX_ITEMS  = int(os.getenv("MAX_ITEMS", "60"))
LOG_LEVEL  = os.getenv("LOG_LEVEL",  "INFO")

# TV/Signage-Einstellungen
DESCRIPTION_CHAR_LIMIT   = int(os.getenv("DESCRIPTION_CHAR_LIMIT", "170"))
FRESH_PUBDATE_WINDOW_MIN = int(os.getenv("FRESH_PUBDATE_WINDOW_MIN", "5"))

# Altersfilter (0 = aus). Nur Items ohne zukünftiges Enddatum werden nach Alter entfernt.
MAX_ITEM_AGE_DAYS     = int(os.getenv("MAX_ITEM_AGE_DAYS", "365"))
ABSOLUTE_MAX_AGE_DAYS = int(os.getenv("ABSOLUTE_MAX_AGE_DAYS", "540"))  # 18 Monate
ACTIVE_GRACE_MIN      = int(os.getenv("ACTIVE_GRACE_MIN", "10"))

# Optionale Schalter, falls du Provider temporär deaktivieren willst
WL_ENABLE  = os.getenv("WL_ENABLE", "1") == "1"
OEBB_ENABLE = os.getenv("OEBB_ENABLE", "1") == "1"  # falls ein oebb-Provider existiert
VOR_ENABLE = os.getenv("VOR_ENABLE", "1") == "1"    # vor.py gibt ohnehin [] zurück, wenn kein Zugang

VIENNA_TZ = ZoneInfo("Europe/Vienna")

# --------------------------------- Logging-Setup ---------------------------------

logging.basicConfig(level=LOG_LEVEL)
log = logging.getLogger("build_feed")

# --------------------------------- Text-Helfer ---------------------------------

def _smart_ellipsis(text: str, limit: int) -> str:
    """Kürzt an der Wortgrenze; fällt auf hartes Limit zurück, wenn nötig."""
    if len(text) <= limit:
        return text
    base = text[:max(0, limit - 1)]
    cut = re.sub(r"\s+\S*$", "", base).rstrip()
    if len(cut) >= int(limit * 0.6):
        return cut + "…"
    return base.rstrip() + "…"

def _to_plain_for_signage(s: str, limit: int = DESCRIPTION_CHAR_LIMIT) -> str:
    """
    Klartext für TV:
    - doppelt unescapen
    - <img> entfernen
    - Block-Tags => " · "
    - restliche HTML-Tags entfernen
    - NBSP zu Space
    - WL-Listen säubern
    - spitze/chevron-Klammern entfernen
    - Whitespace normalisieren, Kürzung an Wortgrenze
    """
    if not s:
        return ""
    s = html.unescape(html.unescape(s))
    s = re.sub(r"<img\b[^>]*>", " ", s, flags=re.I)
    s = re.sub(r"</?(p|br|li|ul|ol|h\d)[^>]*>", " · ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("\u00A0", " ")
    s = re.sub(
        r"Linien:\s*\[([^\]]+)\]",
        lambda m: "Linien: " + ", ".join(t.strip().strip("'\"") for t in m.group(1).split(",")),
        s,
    )
    s = re.sub(r"\bStops:\s*\[[^\]]*\]", " ", s)
    s = re.sub(r"\bBetroffene Haltestellen:\s*[0-9, …]+", " ", s)
    s = s.replace("‹", "").replace("›", "").replace("<", "").replace(">", "")
    s = re.sub(r"\s{2,}", " ", s)
    s = re.sub(r"(?:\s*·\s*){2,}", " · ", s).strip()
    s = s.strip("· ,;:-")
    return _smart_ellipsis(s, limit) if len(s) > limit else s

def _clean_title(raw: str) -> str:
    """
    Titel schön & lesbar:
    - doppelt unescapen
    - evtl. HTML-Tags entfernen
    - alte Präfixe wie "[Quelle/Kategorie] " entfernen
    - ‹ › < > entfernen
    - Whitespace normalisieren
    """
    t = str(raw or "").strip()
    t = html.unescape(html.unescape(t))
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"^\[[^\]]+\]\s*", "", t)
    t = t.replace("‹", "").replace("›", "").replace("<", "").replace(">", "")
    t = re.sub(r"\s{2,}", " ", t).strip(" ·,;:- ").strip()
    return t

# --------------------------------- Datums-Helfer ---------------------------------

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
    SubElement(ch, "ttl").text = "15"
    SubElement(ch, "generator").text = "wien-oepnv (GitHub Actions)"
    return rss, ch

def _stable_pubdate_base(guid: str, now_local: datetime) -> datetime:
    """Tagesstabil: Heute 06:00 + GUID-Offset (<= 1h)."""
    base = now_local.replace(hour=6, minute=0, second=0, microsecond=0)
    h = int(hashlib.md5(guid.encode("utf-8")).hexdigest()[:8], 16)
    return base + timedelta(seconds=(h % 3600))

def _normalize_pubdate(ev: Dict[str, Any], build_now_local: datetime) -> datetime:
    """
    Provider-pubDate verwenden, wenn nicht 'zu frisch'.
    Sonst tagesstabiler Fallback – niemals in der Zukunft gegenüber Build-Zeit.
    """
    dt = ev.get("pubDate")
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        window = timedelta(minutes=FRESH_PUBDATE_WINDOW_MIN)
        if (build_now_local.astimezone(timezone.utc) - dt) >= window:
            return dt
    fb = _stable_pubdate_base(ev["guid"], build_now_local)
    if fb > build_now_local:
        fb = build_now_local - timedelta(seconds=1)
    return fb

def _has_future_end(ev: Dict[str, Any], now_local: datetime) -> bool:
    """True, wenn Enddatum existiert und (mit Gnadenzeit) > jetzt ist."""
    end = ev.get("ends_at")
    if not isinstance(end, datetime):
        return False
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return end >= (now_local - timedelta(minutes=ACTIVE_GRACE_MIN)).astimezone(end.tzinfo)

def _apply_age_filter(items: List[Dict[str, Any]], build_now_local: datetime) -> List[Dict[str, Any]]:
    """
    Entfernt nur Items ohne zukünftiges Enddatum, die älter sind als:
      - ABSOLUTE_MAX_AGE_DAYS (harte Schranke) oder
      - MAX_ITEM_AGE_DAYS (normale Schranke).
    Befristete Langläufer (Ende in der Zukunft) bleiben erhalten.
    """
    if MAX_ITEM_AGE_DAYS <= 0 and ABSOLUTE_MAX_AGE_DAYS <= 0:
        return items

    thr_norm = build_now_local - timedelta(days=MAX_ITEM_AGE_DAYS)
    thr_abs  = build_now_local - timedelta(days=ABSOLUTE_MAX_AGE_DAYS)

    kept = []
    for ev in items:
        pd = ev.get("pubDate")
        if not isinstance(pd, datetime):
            kept.append(ev)  # ohne Datum nicht hart filtern
            continue
        if pd.tzinfo is None:
            pd = pd.replace(tzinfo=timezone.utc)
        pd_local = pd.astimezone(VIENNA_TZ)
        future_end = _has_future_end(ev, build_now_local)

        # harte Schranke
        if not future_end and ABSOLUTE_MAX_AGE_DAYS > 0 and pd_local < thr_abs:
            continue
        # normale Schranke
        if not future_end and MAX_ITEM_AGE_DAYS > 0 and pd_local < thr_norm:
            continue

        kept.append(ev)
    return kept

# --------------------------------- RSS-Item ---------------------------------

def _add_item(ch, ev: Dict[str, Any], build_now_local: datetime) -> None:
    it = SubElement(ch, "item")

    # Schöner Titel (ohne Präfix/Klammern)
    title = _clean_title(ev["title"])
    SubElement(it, "title").text = title

    # TV ohne Interaktion -> neutraler Link
    SubElement(it, "link").text = FEED_LINK

    # Kurzbeschreibung
    short = _to_plain_for_signage(ev.get("description") or "")
    SubElement(it, "description").text = short or title

    # Stabilisiertes Datum
    stable_dt = _normalize_pubdate(ev, build_now_local)
    SubElement(it, "pubDate").text = _fmt_date(stable_dt)

    # GUID + Kategorien (optional hilfreich für Filter)
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

# --------------------------------- Provider laden ---------------------------------

def _load_providers():
    providers = []
    # Wiener Linien
    if WL_ENABLE:
        try:
            from providers import wiener_linien
            providers.append(wiener_linien)
        except Exception as e:
            log.warning("Wiener Linien Provider nicht ladbar: %s", e)
    # ÖBB (optional; darf fehlen)
    if OEBB_ENABLE:
        try:
            from providers import oebb
            providers.append(oebb)
        except Exception:
            # optional – wenn Datei fehlt, einfach ignorieren
            pass
    # VOR/VAO (optional; gibt [] zurück, wenn kein Zugang gesetzt)
    if VOR_ENABLE:
        try:
            from providers import vor
            providers.append(vor)
        except Exception:
            pass
    return providers

# -------------------------------------- Main --------------------------------------

def main() -> None:
    providers = _load_providers()
    if not providers:
        raise SystemExit("Keine Provider geladen – bitte providers/wiener_linien.py prüfen.")

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
                if not ev.get("guid") or ev["guid"] in seen_guids:
                    continue
                seen_guids.add(ev["guid"])
                cleaned.append(ev)
            all_events.extend(cleaned)
            log.info("%s lieferte %d Items", getattr(p, "__name__", str(p)), len(cleaned))
        except Exception as e:
            log.exception("Provider-Fehler bei %s: %s", getattr(p, "__name__", str(p)), e)

    build_now_local = datetime.now(VIENNA_TZ)

    # Altlasten-Filter anwenden (bewahrt befristete Langläufer)
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
