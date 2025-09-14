#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Wiener Linien Provider (OGD) – nur betriebsrelevante Störungen/Hinweise,
keine Roll-/Fahrtreppen- oder Aufzugs-Meldungen.

Änderungen:
- Aufruf von /trafficInfoList OHNE 'aufzugsinfo' und 'fahrtreppeninfo'.
- Expliziter Facility-Filter (Aufzug/Lift/Fahrtreppe/Rolltreppe) für alle Items.
- Titel/Beschreibung gereinigt; pubDate bleibt quellenrein (keine Fallbacks).

Env:
  WL_RSS_URL   (Basis-URL, Secret empfohlen; Fallback: https://www.wienerlinien.at/ogd_realtime)
"""

import hashlib, html, logging, os, re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dateutil import parser as dtparser

WL_BASE = (
    os.getenv("WL_RSS_URL", "").strip()
    or "https://www.wienerlinien.at/ogd_realtime"
).rstrip("/")

log = logging.getLogger(__name__)

def _session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=4,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        raise_on_status=False,
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update({
        "Accept": "application/json",
        "User-Agent": "Origamihase-wien-oepnv/1.8 (+https://github.com/Origamihase/wien-oepnv)"
    })
    return s

S = _session()

# --- Filterlogik -------------------------------------------------------------

# „Betriebsrelevante“ Wörter (lassen wir durch, wenn grundsätzlich aktiv)
KW_RESTRICTION = re.compile(
    r"\b(umleitung|ersatzverkehr|unterbrech|sperr|gesperrt|störung|arbeiten|baustell|einschränk|verspät|ausfall|verkehr)\b",
    re.IGNORECASE
)

# Nicht-betriebsrelevante/Allgemeines (nur als schwaches Ausschluss-Signal)
KW_EXCLUDE = re.compile(
    r"\b(willkommen|gewinnspiel|anzeiger|eröffnung|service(?:-info)?|info(?:rmation)?|fest|keine\s+echtzeitinfo)\b",
    re.IGNORECASE
)

# Facility-ONLY: Aufzug/Lift/Fahrtreppe/Rolltreppe – vollständig ausschließen
FACILITY_ONLY = re.compile(
    r"\b(aufzug|aufzüge|lift|fahrstuhl|fahrtreppe|fahrtreppen|rolltreppe|rolltreppen|aufzugsinfo|fahrtreppeninfo)\b",
    re.IGNORECASE
)

def _is_facility_only(*texts: str) -> bool:
    t = " ".join([x for x in texts if x]).lower()
    return bool(FACILITY_ONLY.search(t))

# --- Zeit-Utils --------------------------------------------------------------

def _iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    if len(s) >= 5 and (s[-5] in "+-") and s[-3] != ":":
        s = s[:-2] + ":" + s[-2:]
    return dtparser.isoparse(s)

def _best_ts(obj: Dict[str, Any]) -> Optional[datetime]:
    t = obj.get("time") or {}
    cand = [
        _iso(t.get("start")), _iso(t.get("end")),
        _iso(obj.get("updated")), _iso(obj.get("timestamp")),
        _iso((obj.get("attributes") or {}).get("lastUpdate")),
        _iso((obj.get("attributes") or {}).get("created")),
    ]
    return next((x for x in cand if x), None)

def _times(obj: Dict[str, Any]) -> Tuple[Optional[datetime], Optional[datetime]]:
    t = obj.get("time") or {}
    return _iso(t.get("start")), _iso(t.get("end"))

def _is_active(start: Optional[datetime], end: Optional[datetime], now: datetime) -> bool:
    if start and start > now:
        return False
    if end and end < (now - timedelta(minutes=10)):
        return False
    return True

# --- Hilfsfunktionen ---------------------------------------------------------

def _as_list(val) -> List[Any]:
    if val is None: return []
    return list(val) if isinstance(val, (list, tuple, set)) else [val]

def _tok(v: Any) -> str:
    return re.sub(r"[^A-Za-z0-9+]", "", str(v)).upper()

def _norm_title(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())

def _guid(*parts: str) -> str:
    base = "|".join(p or "" for p in parts)
    return hashlib.md5(base.encode("utf-8")).hexdigest()

_CLOSED_HINTS = (
    "beendet","abgeschlossen","geschlossen","fertig","resolved",
    "finished","inactive","inaktiv","done","closed","nicht aktiv","ended","ende"
)

def _is_closed(obj: Dict[str, Any]) -> bool:
    attrs = obj.get("attributes") or {}
    candidates = [str(obj.get("status") or ""), str(attrs.get("status") or ""), str(attrs.get("state") or "")]
    active_flags = []
    for key in ("active","isActive","is_active","enabled"):
        if key in obj: active_flags.append(bool(obj.get(key)))
        if key in attrs: active_flags.append(bool(attrs.get(key)))
    if any(flag is False for flag in active_flags):
        return True
    val = " ".join(candidates).strip().lower()
    return any(h in val for h in _CLOSED_HINTS)

# --- API-Calls ----------------------------------------------------------------

def _get_json(path: str, params: Optional[List[tuple]] = None, timeout: int = 20) -> Dict[str, Any]:
    url = f"{WL_BASE.rstrip('/')}/{path.lstrip('/')}"
    r = S.get(url, params=params or None, timeout=timeout)
    r.raise_for_status()
    return r.json()

def _fetch_traffic_infos(timeout: int = 20) -> Iterable[Dict[str, Any]]:
    # WICHTIG: Aufzug/Fahrtreppe nicht mehr anfragen
    params = [("name","stoerunglang"),("name","stoerungkurz")]
    data = _get_json("trafficInfoList", params=params, timeout=timeout)
    return (data.get("data", {}) or {}).get("trafficInfos", []) or []

def _fetch_news(timeout: int = 20) -> Iterable[Dict[str, Any]]:
    data = _get_json("newsList", timeout=timeout)
    return (data.get("data", {}) or {}).get("pois", []) or []

# --- Public API ---------------------------------------------------------------

def fetch_events(timeout: int = 20) -> List[Dict[str, Any]]:
    """
    Liefert NUR aktive betriebsrelevante Beeinträchtigungen.
    Facility-Only (Aufzug/Lift/Fahrtreppe/Rolltreppe) wird konsequent ausgeschlossen.
    pubDate: ausschließlich quellenbasiert (start/best_ts). Kein 'now'-Fallback.
    """
    now = datetime.now(timezone.utc)
    raw: List[Dict[str, Any]] = []

    # A) TrafficInfos (ohne Facility)
    try:
        for ti in _fetch_traffic_infos(timeout=timeout):
            if _is_closed(ti):
                continue

            title = (ti.get("title") or ti.get("name") or "Meldung").strip()
            desc  = (ti.get("description") or "").strip()
            attrs = ti.get("attributes") or {}
            fulltext = " ".join([title, desc, str(attrs.get("status") or ""), str(attrs.get("state") or "")])

            # Facility-Only strikt verwerfen
            if _is_facility_only(title, desc):
                continue

            ts_best = _best_ts(ti)
            start = _iso((ti.get("time") or {}).get("start")) or ts_best
            end   = _iso((ti.get("time") or {}).get("end"))
            if not _is_active(start, end, now):
                continue

            # schwaches „Thema passt nicht“-Signal
            if KW_EXCLUDE.search(fulltext) and not KW_RESTRICTION.search(fulltext):
                continue

            rel_lines = _as_list(ti.get("relatedLines") or attrs.get("relatedLines"))
            rel_stops = _as_list(ti.get("relatedStops") or attrs.get("relatedStops"))
            lines_str = ", ".join(str(x).strip() for x in rel_lines if str(x).strip())
            extras = []
            for k in ("status","state","station","location","reason","towards"):
                if attrs.get(k):
                    extras.append(f"{k.capitalize()}: {html.escape(str(attrs[k]))}")
            if lines_str:
                extras.append(f"Linien: {html.escape(lines_str)}")

            raw.append({
                "source": "Wiener Linien",
                "category": "Störung",
                "title": title,
                "desc": html.escape(desc),
                "extras": extras,
                "lines": { _tok(x) for x in rel_lines if str(x).strip() },
                "stops": { _tok(x) for x in rel_stops if str(x).strip() },
                "pubDate": start,           # NUR Quelle (kann None sein)
                "starts_at": start,
                "ends_at": end,
            })
    except Exception as e:
        logging.exception("WL trafficInfoList fehlgeschlagen: %s", e)

    # B) News/Hinweise (nur betriebsrelevant, ohne Facility)
    try:
        for poi in _fetch_news(timeout=timeout):
            if _is_closed(poi):
                continue

            title = (poi.get("title") or "Hinweis").strip()
            desc  = (poi.get("description") or "").strip()
            attrs = poi.get("attributes") or {}
            text_for_filter = " ".join([
                title, poi.get("subtitle") or "", desc,
                str(attrs.get("status") or ""), str(attrs.get("state") or ""),
            ])

            # Facility-Only strikt verwerfen
            if _is_facility_only(title, desc, poi.get("subtitle") or ""):
                continue

            ts_best = _best_ts(poi)
            start = _iso((poi.get("time") or {}).get("start")) or ts_best
            end   = _iso((poi.get("time") or {}).get("end"))
            if not _is_active(start, end, now):
                continue

            # nur betriebsrelevante Themen
            if not KW_RESTRICTION.search(text_for_filter):
                continue

            rel_lines = _as_list(poi.get("relatedLines") or attrs.get("relatedLines"))
            rel_stops = _as_list(poi.get("relatedStops") or attrs.get("relatedStops"))
            lines_str = ", ".join(str(x).strip() for x in rel_lines if str(x).strip())
            extras = []
            if poi.get("subtitle"):
                extras.append(html.escape(poi["subtitle"]))
            for k in ("station","location","towards"):
                if attrs.get(k):
                    extras.append(f"{k.capitalize()}: {html.escape(str(attrs[k]))}")
            if lines_str:
                extras.append(f"Linien: {html.escape(lines_str)}")

            raw.append({
                "source": "Wiener Linien",
                "category": "Hinweis",
                "title": title,
                "desc": html.escape(desc),
                "extras": extras,
                "lines": { _tok(x) for x in rel_lines if str(x).strip() },
                "stops": { _tok(x) for x in rel_stops if str(x).strip() },
                "pubDate": start,           # NUR Quelle (kann None sein)
                "starts_at": start,
                "ends_at": end,
            })
    except Exception as e:
        logging.exception("WL newsList fehlgeschlagen: %s", e)

    # C) Dedupe: Thema (Titel normalisiert) + Linien
    buckets: Dict[str, Dict[str, Any]] = {}
    for ev in raw:
        key = _guid("wl", ev["category"], _norm_title(ev["title"]), ",".join(sorted(ev["lines"])))
        b = buckets.get(key)
        if not b:
            buckets[key] = {
                "source": "Wiener Linien",
                "category": ev["category"],
                "title": ev["title"],
                "desc_base": ev["desc"],
                "extras": list(ev["extras"]),
                "lines": set(ev["lines"]),
                "stops": set(ev["stops"]),
                "pubDate": ev["pubDate"],    # kann None sein
                "starts_at": ev["starts_at"],
                "ends_at": ev["ends_at"],
            }
        else:
            b["stops"].update(ev["stops"])
            b["lines"].update(ev["lines"])
            if ev["pubDate"] and (not b["pubDate"] or ev["pubDate"] < b["pubDate"]):
                b["pubDate"] = ev["pubDate"]
            be, ee = b["ends_at"], ev["ends_at"]
            b["ends_at"] = None if (be is None or ee is None) else max(be, ee)
            for x in ev["extras"]:
                if x not in b["extras"]:
                    b["extras"].append(x)

    # D) finale Items
    items: List[Dict[str, Any]] = []
    for b in buckets.values():
        title = html.escape(b["title"])
        desc = b["desc_base"]
        if b["extras"]:
            desc = (desc + ("<br/>" if desc else "") + "<br/>".join(b["extras"]))
        if b["stops"]:
            stops_list = sorted(b["stops"])
            stops_str = ", ".join(stops_list[:15]) + (" …" if len(stops_list) > 15 else "")
            desc += ("<br/>Betroffene Haltestellen: " + html.escape(stops_str))
        guid = _guid("wl", b["category"], _norm_title(b["title"]), ",".join(sorted(b["lines"])))
        items.append({
            "source": "Wiener Linien",
            "category": b["category"],
            "title": title,
            "description": desc,
            "link": f"{WL_BASE}",
            "guid": guid,
            "pubDate": b["pubDate"],      # None erlaubt
            "starts_at": b["starts_at"],
            "ends_at": b["ends_at"],
        })

    # Sortierstrategie: bevorzugt Items mit Datum; sonst stabiler Hash
    items.sort(key=lambda x: (0, x["pubDate"]) if x["pubDate"] else (1, hashlib.md5(x["guid"].encode()).hexdigest()))
    return items
