#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RSS 2.0 Feed für aktive ÖPNV-Beeinträchtigungen (Wien & nahe Umgebung), TV-tauglich.

Wichtig:
- <pubDate> kommt ausschließlich aus der Quelle (nie künstlich).
- Items OHNE pubDate bleiben im Feed; intern nutzen wir:
      ref_dt = pubDate oder starts_at oder first_seen (erstmals gesehen).
- Befristete Langläufer (Ende in der Zukunft) bleiben erhalten (ACTIVE_GRACE_MIN).
- GUID-basierte Duplikatsunterdrückung.
- Persistenter State (JSON) nur für 'first_seen'.
- State enthält NUR noch Items, die im finalen Feed sind (alles andere wird entfernt).
- Optionales Metadatum <first_seen> je Item (nur Metadaten; für TV unsichtbar).

Spezialfall:
- „Busse halten bei Neubaugasse 69“ erhält ein forciertes first_seen (2023-08-08 Europe/Vienna),
  solange die Meldung im Feed vorkommt (nur dann wird es gespeichert/ausgegeben).

ENV:
  FEED_TITLE, FEED_LINK, FEED_DESC, OUT_PATH
  MAX_ITEMS, MAX_ITEM_AGE_DAYS, ABSOLUTE_MAX_AGE_DAYS, ACTIVE_GRACE_MIN
  DESCRIPTION_CHAR_LIMIT, LOG_LEVEL
  WL_ENABLE, OEBB_ENABLE, VOR_ENABLE
  STATE_PATH                (Default: data/first_seen.json)
"""

from __future__ import annotations

import os, re, html, json, logging, hashlib, sys
from typing import List, Dict, Any, Optional, Tuple
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

# Persistenter State für first_seen (schlank)
STATE_PATH = os.getenv("STATE_PATH", "data/first_seen.json")

VIENNA_TZ = ZoneInfo("Europe/Vienna")

# ------------------------- Logging -------------------------
logging.basicConfig(level=LOG_LEVEL)
log = logging.getLogger("build_feed")

# ------------------------- State-Handling (nur first_seen) -------------------------
# Struktur: { "<guid>": "2025-09-13T21:04:00Z", ... }
_STATE: Dict[str, datetime] = {}

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
        _STATE = {k: _parse_iso(v) for k, v in (raw or {}).items() if _parse_iso(v)}
    except FileNotFoundError:
        _STATE = {}
    except Exception as e:
        log.warning("State konnte nicht geladen werden (%s) – starte leer.", e)
        _STATE = {}

def _save_state(visible_guids: List[str], now_utc: datetime, forced_map: Dict[str, datetime]) -> None:
    """
    Speichert nur first_seen für GUIDs, die im finalen Feed (visible_guids) enthalten sind.
    - Für neue GUIDs: first_seen = forced_map.get(guid) oder now_utc
    - Für bestehende GUIDs: first_seen bleibt, außer forced_map liefert einen Override
    - Alle GUIDs, die nicht sichtbar sind, werden entfernt (State bleibt schlank)
    """
    keep: Dict[str, datetime] = {}
    for g in visible_guids:
        if g in forced_map:
            keep[g] = forced_map[g]
        elif g in _STATE and isinstance(_STATE[g], datetime):
            keep[g] = _STATE[g]
        else:
            keep[g] = now_utc

    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    out = {g: _iso_utc(dt) for g, dt in keep.items()}
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, sort_keys=True)
    # internen State aktualisieren
    _STATE.clear()
    _STATE.update(keep)
    log.info("State gespeichert: %s (first_seen=%d)", STATE_PATH, len(out))

def _first_seen(guid: str) -> Optional[datetime]:
    return _STATE.get(guid)

# ------------------------- Forcierte first_seen-Overrides -------------------------
_FORCED_FS: Tuple[Tuple[re.Pattern, datetime], ...] = (
    (
        re.compile(r"^Busse\s+halten\s+bei\s+Neubaugasse\s+69\b", re.IGNORECASE),
        datetime(2023, 8, 8, 0, 0, tzinfo=VIENNA_TZ).astimezone(timezone.utc)
    ),
)

def _forced_first_seen_for_title(title: str) -> Optional[datetime]:
    for rx, dt_utc in _FORCED_FS:
        if rx.search(title or ""):
            return dt_utc
    return None

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
    # Reihenfolge: pubDate → starts_at → forced_first_seen → saved first_seen
    return (
        _get_dt(ev.get("pubDate"))
        or _get_dt(ev.get("starts_at"))
        or _get_dt(ev.get("forced_first_seen"))
        or _first_seen(ev.get("guid", ""))
    )

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
def _add_item(ch, ev: Dict[str, Any], fs_map: Dict[str, datetime]) -> None:
    it = SubElement(ch, "item")
    title = _clean_title(ev["title"])
    SubElement(it, "title").text = title
    SubElement(it, "link").text = FEED_LINK
    short = _to_plain_for_signage(ev.get("description") or "")
    SubElement(it, "description").text = short or title

    # pubDate nur, wenn aus Quelle vorhanden
    pd = _get_dt(ev.get("pubDate"))
    if pd:
        SubElement(it, "pubDate").text = _fmt_date(pd)

    # Metadatum: first_seen (nur für im Feed sichtbare Items)
    fs = fs_map.get(ev["guid"])
    if fs:
        SubElement(it, "first_seen").text = _fmt_date(fs)

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

    # 1) Provider abfragen und Events einsammeln
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
                # evtl. forcierter first_seen (nur an ev angehängt; Speicherung später)
                forced = _forced_first_seen_for_title(html.unescape(ev.get("title") or ""))
                if forced is not None:
                    ev["forced_first_seen"] = forced
                seen_guids.add(ev["guid"])
                all_events.append(ev)
                added += 1
            log.info("%s lieferte %d Items", getattr(p, "__name__", str(p)), added)
        except Exception as e:
            log.exception("Provider-Fehler bei %s: %s", getattr(p, "__name__", str(p)), e)

    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(VIENNA_TZ)

    # 2) Altersfilter anwenden (nutzt pubDate/starts_at/forced_first_seen/_STATE)
    all_events = _apply_age_filter(all_events, now_local)

    # 3) Sortieren & deckeln
    all_events.sort(key=_stable_order_key)
    if MAX_ITEMS > 0 and len(all_events) > MAX_ITEMS:
        all_events = all_events[:MAX_ITEMS]

    # 4) State NUR für sichtbare Items aktualisieren
    visible_guids = [ev["guid"] for ev in all_events]
    forced_map = {
        ev["guid"]: ev["forced_first_seen"]  # nur dort, wo gesetzt
        for ev in all_events
        if ev.get("forced_first_seen") is not None
    }
    _save_state(visible_guids, now_utc, forced_map)

    # 5) RSS bauen
    rss, ch = _rss_root(FEED_TITLE, FEED_LINK, FEED_DESC)
    # Map für Ausgabe (nur sichtbare Items)
    fs_out_map: Dict[str, datetime] = {g: _STATE[g] for g in visible_guids if g in _STATE}
    for ev in all_events:
        _add_item(ch, ev, fs_out_map)

    # 6) Schreiben
    _write_xml(rss, OUT_PATH)
    log.info("Fertig: %d Items im Feed", len(all_events))

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logging.exception("Abbruch: %s", e)
        sys.exit(1)
