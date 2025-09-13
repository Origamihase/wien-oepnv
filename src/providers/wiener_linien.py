import hashlib, html, logging, re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple, Set

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dateutil import parser as dtparser

BASE = "https://www.wienerlinien.at/ogd_realtime"

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
        "User-Agent": "Origamihase-wien-oepnv/1.2 (+https://github.com/Origamihase/wien-oepnv)"
    })
    return s

S = _session()

# --- Heuristiken ---------------------------------------------------------------
KW_RESTRICTION = re.compile(
    r"\b(umleitung|ersatzverkehr|unterbrech|sperr|gesperrt|störung|aufzug|fahrtreppe|arbeiten|baustell|einschränk|verspät|ausfall)\b",
    re.IGNORECASE
)
# raus, sofern KEIN Restriktionswort vorkommt:
KW_EXCLUDE = re.compile(
    r"\b(willkommen|gewinnspiel|anzeiger|eröffnung|service|info(?:rmation)?|fest|keine\s+echtzeitinfo)\b",
    re.IGNORECASE
)

def _iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    # 2025-09-13T21:37:14+0000 -> +00:00
    if len(s) >= 5 and (s[-5] in "+-") and s[-3] != ":":
        s = s[:-2] + ":" + s[-2:]
    return dtparser.isoparse(s)

def _best_ts(obj: Dict[str, Any]) -> Optional[datetime]:
    """Robuster Zeitstempel (Start/Update) für pubDate."""
    t = obj.get("time") or {}
    cand = [
        _iso(t.get("start")), _iso(t.get("end")),
        _iso(obj.get("updated")), _iso(obj.get("timestamp")),
        _iso((obj.get("attributes") or {}).get("lastUpdate")),
        _iso((obj.get("attributes") or {}).get("created")),
    ]
    return next((x for x in cand if x), None)

def _is_active(start: Optional[datetime], end: Optional[datetime], now: datetime) -> bool:
    # aktiv: begonnen & nicht beendet; 10-min-Gnade verhindert Flackern
    if start and start > now:
        return False
    if end and end < (now - timedelta(minutes=10)):
        return False
    return True

def _as_list(val) -> List[Any]:
    if val is None:
        return []
    return list(val) if isinstance(val, (list, tuple, set)) else [val]

def _tok(v: Any) -> str:
    return re.sub(r"[^A-Za-z0-9+]", "", str(v)).upper()

def _norm_title(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())

def _guid(*parts: str) -> str:
    base = "|".join(p or "" for p in parts)
    return hashlib.md5(base.encode("utf-8")).hexdigest()

def _fetch_traffic_infos(timeout: int = 20):
    params = [("name","stoerunglang"),("name","stoerungkurz"),
              ("name","aufzugsinfo"),("name","fahrtreppeninfo")]
    r = S.get(f"{BASE}/trafficInfoList", params=params, timeout=timeout)
    r.raise_for_status()
    return (r.json().get("data", {}) or {}).get("trafficInfos", []) or []

def _fetch_news(timeout: int = 20):
    r = S.get(f"{BASE}/newsList", timeout=timeout)
    r.raise_for_status()
    return (r.json().get("data", {}) or {}).get("pois", []) or []

def fetch_events(timeout: int = 20) -> List[Dict[str, Any]]:
    """
    Liefert NUR aktive Beeinträchtigungen. Dedupe über (Kategorie, Titel, Linien).
    Ergebnis-Item:
      {source, category, title, description, link, guid, pubDate}
    """
    now = datetime.now(timezone.utc)
    raw: List[Dict[str, Any]] = []

    # A) TrafficInfos (Störungen/Aufzug/Fahrtreppe)
    try:
        for ti in _fetch_traffic_infos(timeout=timeout):
            ts_best = _best_ts(ti)
            start = _iso((ti.get("time") or {}).get("start")) or ts_best
            end   = _iso((ti.get("time") or {}).get("end"))
            if not _is_active(start, end, now):
                continue

            title = (ti.get("title") or ti.get("name") or "Meldung").strip()
            attrs = ti.get("attributes") or {}
            fulltext = " ".join([title, ti.get("description") or "", str(attrs.get("status") or "")])

            if KW_EXCLUDE.search(fulltext) and not KW_RESTRICTION.search(fulltext):
                continue

            rel_lines = _as_list(ti.get("relatedLines") or attrs.get("relatedLines"))
            rel_stops = _as_list(ti.get("relatedStops") or attrs.get("relatedStops"))

            lines_str = ", ".join(str(x).strip() for x in rel_lines if str(x).strip())
            extras = []
            for k in ("status","station","location","reason","towards"):
                if attrs.get(k):
                    extras.append(f"{k.capitalize()}: {html.escape(str(attrs[k]))}")
            if lines_str:
                extras.append(f"Linien: {html.escape(lines_str)}")

            raw.append({
                "category": "Störung",
                "title": title,
                "desc": html.escape(ti.get("description") or ""),
                "extras": extras,
                "lines": { _tok(x) for x in rel_lines if str(x).strip() },
                "stops": { _tok(x) for x in rel_stops if str(x).strip() },
                "pubDate": start or ts_best or now,
            })
    except Exception as e:
        logging.exception("WL trafficInfoList fehlgeschlagen: %s", e)

    # B) News/Hinweise (nur mit echter Einschränkung)
    try:
        for poi in _fetch_news(timeout=timeout):
            ts_best = _best_ts(poi)
            start = _iso((poi.get("time") or {}).get("start")) or ts_best
            end   = _iso((poi.get("time") or {}).get("end"))
            if not _is_active(start, end, now):
                continue

            title = (poi.get("title") or "Hinweis").strip()
            attrs = poi.get("attributes") or {}
            text_for_filter = " ".join([title, poi.get("subtitle") or "", poi.get("description") or "", str(attrs.get("status") or "")])
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
                "category": "Hinweis",
                "title": title,
                "desc": html.escape(poi.get("description") or ""),
                "extras": extras,
                "lines": { _tok(x) for x in rel_lines if str(x).strip() },
                "stops": { _tok(x) for x in rel_stops if str(x).strip() },
                "pubDate": start or ts_best or now,
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
                "pubDate": ev["pubDate"],
            }
        else:
            b["stops"].update(ev["stops"])
            b["lines"].update(ev["lines"])
            if ev["pubDate"] and ev["pubDate"] < b["pubDate"]:
                b["pubDate"] = ev["pubDate"]
            for x in ev["extras"]:
                if x not in b["extras"]:
                    b["extras"].append(x)

    # D) finale Items
    items: List[Dict[str, Any]] = []
    for b in buckets.values():
        # sichere Titel (keine rohen spitzen Klammern)
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
            "title": title,  # bereits escapt
            "description": desc,  # HTML, wird später für TV gekürzt/gestrippt
            "link": "https://www.wienerlinien.at/open-data",
            "guid": guid,
            "pubDate": b["pubDate"],
        })

    items.sort(key=lambda x: x["pubDate"], reverse=True)
    return items
