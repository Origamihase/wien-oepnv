#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RSS 2.0 Feed für aktive ÖPNV-Beeinträchtigungen (Wien & nahe Umgebung), TV-tauglich.

Wichtig:
- <pubDate> wird ausschließlich aus der QUELLE übernommen (nie künstlich gesetzt).
- Items OHNE pubDate bleiben im Feed; für Sortierung/Altersfilter nutzen wir intern:
    ref_dt = pubDate oder starts_at oder first_seen (erstmals gesehen).
- Befristete Langläufer (Ende in der Zukunft) bleiben erhalten (ACTIVE_GRACE_MIN).
- Duplikate werden über GUID unterdrückt.
- State-Datei 'first_seen'/'last_seen' wird gepflegt (JSON).

ENV:
  FEED_TITLE, FEED_LINK, FEED_DESC, OUT_PATH
  MAX_ITEMS, MAX_ITEM_AGE_DAYS, ABSOLUTE_MAX_AGE_DAYS, ACTIVE_GRACE_MIN
  DESCRIPTION_CHAR_LIMIT, LOG_LEVEL
  WL_ENABLE, OEBB_ENABLE, VOR_ENABLE
  STATE_PATH                (Default: data/first_seen.json)
  STATE_RETENTION_DAYS      (Default: 1095 = 3 Jahre)

Provider liefern Events als Dict mit Keys:
  source, category, title, description, link, guid,
  pubDate (datetime|None), starts_at (datetime|None), ends_at (datetime|None)
"""

from __future__ import annotations

import os, re, html, json, logging, hashlib, sys
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from xml.etree.ElementTree import Element, SubElement, tostring
from zoneinfo import ZoneInfo

# ------------------------- Konfiguration -------------------------

FEED_TITLE = os.getenv("FEED_TITLE", "ÖPNV Störungen Wien & Umgebung")
FEED_LINK  = os.getenv("FEED_LINK",  "https://github.com/Origamihase/wien-oepnv")
FEED_DESC  = os.getenv("FEED_DESC",  "Aktive Störungen/Baustellen/Einschränkungen aus offiziellen Quellen")
OUT_PATH   = os.getenv("OUT_PATH",   "docs/feed.xml")

MAX_ITEMS  = int(os.getenv("MAX_ITEMS", "60"))
LOG_LEVEL  = os.getenv("LOG_LEVEL",  "INFO")

DESCRIPTION_CHAR_LIMIT   = int(os.getenv("DESCRIPTION_CHAR_LIMIT", "170"))

# Altersfilter (nur für Items ohne zukünftiges Enddatum)
MAX_ITEM_AGE_DAYS     = int(os.getenv("MAX_ITEM_AGE_DAYS", "365"))
ABSOLUTE_MAX_AGE_DAYS = int(os.getenv("ABSOLUTE_MAX_AGE_DAYS", "540"))  # 18 Monate
ACTIVE_GRACE_MIN      = int(os.getenv("ACTIVE_GRACE_MIN", "10"))

# Provider-Schalter
WL_ENABLE   = os.getenv("WL_ENABLE", "1") == "1"
OEBB_ENABLE = os.getenv("OEBB_ENABLE", "1") == "1"
VOR_ENABLE  = os.getenv("VOR_ENABLE", "1") == "1"

# Persistenter State für first_seen / last_seen
STATE_PATH = os.getenv("STATE_PATH", "data/first_seen.json")
STATE_RETENTION_DAYS = int(os.getenv("STATE_RETENTION_DAYS", "1095"))

VIENNA_TZ = ZoneInfo("Europe/Vienna")

# ------------------------- Logging -------------------------
logging.basicConfig(level=LOG_LEVEL)
log = logging.getLogger("build_feed")

# ------------------------- State-Handling -------------------------
_STATE: Dict[str, Dict[str, datetime]] = {"first_seen": {}, "last_seen": {}}

def _parse_iso(s: str | None) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None

def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _load_state() -> None:
    global _STATE
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        fs = {k: _parse_iso(v) for k, v in (raw.get("first_seen") or {}).items()}
        ls = {k: _parse_iso(v) for k, v in (raw.get("last_seen") or {}).items()}
        _STATE = {"first_seen": {k: v for k, v in fs.items() if v},
                  "last_seen":  {k: v for k, v in ls.items() if v}}
    except FileNotFoundError:
        _STATE = {"first_seen": {}, "last_seen": {}}
    except Exception as e:
        log.warning("State konnte nicht geladen werden (%s) – starte leer.", e)
        _STATE = {"first_seen": {}, "last_seen": {}}

def _save_state(now_utc: datetime) -> None:
    # alte Einträge aufräumen (optional)
    if STATE_RETENTION_DAYS > 0:
        cutoff = now_utc - timedelta(days=STATE_RETENTION_DAYS)
        drop = [g for g, dt in _STATE["last_seen"].items() if dt and dt < cutoff]
        for g in drop:
            _STATE["last_seen"].pop(g, None)
            _STATE["first_seen"].pop(g, None)

    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    out = {
        "first_seen": {g: _iso_utc(dt) for g, dt in _STATE["first_seen"].items() if dt},
        "last_seen":  {g: _iso_utc(dt) for g, dt in _STATE["last_seen"].items() if dt},
    }
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, sort_keys=True)
    log.info("State gespeichert: %s (first_seen=%d)", STATE_PATH, len(out["first_seen"]))

def _first_seen(guid: str) -> Optional[datetime]:
    return _STATE["first_seen"].get(guid)

def _touch_seen(guid: str, now_utc: datetime) -> None:
    if guid not in _STATE["first_seen"]:
        _STATE["first_seen"][guid] = now_utc
    _STATE["last_seen"][guid] = now_utc

# ------------------------- Text-Utils -------------------------
def _smart_ellipsis(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    base = text[:max(0, limit - 1)]
    cut = re.sub(r"\s+\S*$", "", base).rstrip()
    if len(cut) >= int(limit * 0.6):
        return cut + "…"
    return base.rstrip() + "…"

def _to_plain_for_signage(s: str, limit: int = DESCRIPTION_CHAR_LIMIT) -> str:
    if not s:
        return ""
    s = html.unescape(html.unescape(s))
    s = re.sub(r"<img\b[^>]*>", " ", s, flags=re.I)
    s = re.sub(r"</?(p|br|li|ul|ol|h\d)[^>]*>", " · ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("\u00A0", " ")
    s = re.sub(r"Linien:\s*\[([^\]]+)\]",
               lambda m: "Linien: " + ", ".join(t.strip().strip("'\"") for t in m.group(1).split(",")), s)
    s = re.sub(r"\bStops:\s*\[[^\]]*\]", " ", s)
    s = re.sub(r"\bBetroffene Haltestellen:\s*[0-9, …]+", " ", s)
    s = s.replace("‹", "").replace("›", "").replace("<", "").replace(">", "")
    s = re.sub(r"\s{2,}", " ", s)
    s = re.sub(r"(?:\s*·\s*){2,}", " · ", s).strip()
    s = s.strip("· ,;:-")
    return _smart_ellipsis(s, limit) if len(s) > limit else s

def _clean_title(raw: str) -> str:
    t = str(raw or "").strip()
    t = html.unescape(html.unescape(t))
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"^\[[^\]]+\]\s*", "", t)
    t = t.replace("‹", "").replace("›", "").replace("<", "").replace(">", "")
    t = re.sub(r"\s{2,}", " ", t).strip(" ·,;:- ").strip()
    return t

# ------------------------- Date/Feed Utils -------------------------
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

def _get_dt(val: Any) -> Optional[datetime]:
    from datetime import datetime as _dt
    if isinstance(val, _dt):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val
    return None

def _event_ref_dt(ev: Dict[str, Any]) -> Optional[datetime]:
    # Reihenfolge: pubDate → starts_at → first_seen
    return _get_dt(ev.get("pubDate")) or _get_dt(ev.get("starts_at")) or _first_seen(ev.get("guid", ""))

def _has_future_end(ev: Dict[str, Any], now_local: datetime) -> bool:
    end = _get_dt(ev.get("ends_at"))
    if not end:
        return False
    return end >= (now_local - timedelta(minutes=ACTIVE_GRACE_MIN)).astimezone(end.tzinfo)

def _apply_age_filter(items: List[Dict[str, Any]], build_now_local: datetime) -> List[Dict[str, Any]]:
    if MAX_ITEM_AGE_DAYS <= 0 and ABSOLUTE_MAX_AGE_DAYS <= 0:
        return items
    thr_norm = build_now_local - timedelta(days=MAX_ITEM_AGE_DAYS)
    thr_abs  = build_now_local - timedelta(days=ABSOLUTE_MAX_AGE_DAYS)

    kept = []
    for ev in items:
        ref = _event_ref_dt(ev)
        if not _has_future_end(ev, build_now_local):
            if ref is not None:
                ref_local = ref.astimezone(VIENNA_TZ)
                if ABSOLUTE_MAX_AGE_DAYS > 0 and ref_local < thr_abs:
                    continue
                if MAX_ITEM_AGE_DAYS > 0 and ref_local < thr_norm:
                    continue
        kept.append(ev)
    return kept

def _stable_order_key(ev: Dict[str, Any]) -> tuple:
    ref = _event_ref_dt(ev)
    if ref:
        return (0, -int(ref.timestamp()), ev.get("guid", ""))
    h = int(hashlib.md5((ev.get("guid", "") or "").encode("utf-8")).hexdigest(), 16)
    return (1, h)

# ------------------------- RSS Item -------------------------
def _add_item(ch, ev: Dict[str, Any]) -> None:
    it = SubElement(ch, "item")
    title = _clean_title(ev["title"])
    SubElement(it, "title").text = title
    SubElement(it, "link").text = FEED_LINK
    short = _to_plain_for_signage(ev.get("description") or "")
    SubElement(it, "description").text = short or title
    pd = _get_dt(ev.get("pubDate"))
    if pd:
        SubElement(it, "pubDate").text = _fmt_date(pd)
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

# ------------------------- Provider laden -------------------------
def _load_providers():
    providers = []
    if WL_ENABLE:
        try:
            from providers import wiener_linien
            providers.append(wiener_linien)
        except Exception as e:
            log.warning("Wiener Linien Provider nicht ladbar: %s", e)
    if OEBB_ENABLE:
        try:
            from providers import oebb
            providers.append(oebb)
        except Exception:
            pass
    if VOR_ENABLE:
        try:
            from providers import vor
            providers.append(vor)
        except Exception:
            pass
    return providers

# ------------------------- Main -------------------------
def main() -> None:
    _load_state()

    providers = _load_providers()
    if not providers:
        raise SystemExit("Keine Provider geladen – bitte providers/wiener_linien.py prüfen.")

    all_events: List[Dict[str, Any]] = []
    seen_guids: set[str] = set()

    for p in providers:
        try:
            events = p.fetch_events()
            added = 0
            for ev in events:
                if not {"source","category","title","description","link","guid"}.issubset(ev.keys()):
                    continue
                if not ev.get("guid") or ev["guid"] in seen_guids:
                    continue
                # Zeiten normalisieren
                for k in ("pubDate","starts_at","ends_at"):
                    if not isinstance(ev.get(k), datetime):
                        ev[k] = None
                seen_guids.add(ev["guid"])
                all_events.append(ev)
                added += 1
            log.info("%s lieferte %d Items", getattr(p, "__name__", str(p)), added)
        except Exception as e:
            log.exception("Provider-Fehler bei %s: %s", getattr(p, "__name__", str(p)), e)

    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(VIENNA_TZ)

    # *** WICHTIG: first_seen/last_seen vor dem Altersfilter pflegen ***
    for ev in all_events:
        _touch_seen(ev["guid"], now_utc)

    # Altersfilter anwenden (bewahrt befristete Langläufer)
    all_events = _apply_age_filter(all_events, now_local)

    # Sortieren & deckeln
    all_events.sort(key=_stable_order_key)
    if MAX_ITEMS > 0 and len(all_events) > MAX_ITEMS:
        all_events = all_events[:MAX_ITEMS]

    # RSS bauen
    rss, ch = _rss_root(FEED_TITLE, FEED_LINK, FEED_DESC)
    for ev in all_events:
        _add_item(ch, ev)

    # Schreiben
    _write_xml(rss, OUT_PATH)
    _save_state(now_utc)
    log.info("Fertig: %d Items im Feed", len(all_events))

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logging.exception("Abbruch: %s", e)
        sys.exit(1)
