import hashlib, html, logging, re
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dateutil import parser as dtparser

BASE = "https://www.wienerlinien.at/ogd_realtime"

def _session() -> requests.Session:
    """Robuste HTTP-Session mit Retries & sauberem User-Agent."""
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
        "User-Agent": "Origamihase-wien-oepnv/1.0 (+https://github.com/Origamihase/wien-oepnv)"
    })
    return s

S = _session()

# --- Hilfen --------------------------------------------------------------------

KW_RESTRICTION = [
    # sichere, restriktionsnahe Schlüsselwörter für News/Hinweise
    r"\bumleitung\b", r"\bersatzverkehr\b", r"\bunterbrech", r"\bsperr", r"\bgesperrt\b",
    r"\bstörung\b", r"\baufzug\b", r"\bfahrtreppe\b", r"\barbeiten\b", r"\bbaustell",
    r"\bbeeinträchtig", r"\beinschränkung", r"\bverspät", r"\bausfall\b",
]
KW_RE = re.compile("|".join(KW_RESTRICTION), re.IGNORECASE)

def _iso(s: Optional[str]) -> Optional[datetime]:
    """ISO-Zeit robust parsen (Z, +0200, +02:00)."""
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    if len(s) >= 5 and (s[-5] in "+-") and s[-3] != ":":
        s = s[:-2] + ":" + s[-2:]
    return dtparser.isoparse(s)

def _times(obj: Dict[str, Any]) -> Tuple[Optional[datetime], Optional[datetime]]:
    t = obj.get("time") or {}
    return _iso(t.get("start")), _iso(t.get("end"))

def _is_active(start: Optional[datetime], end: Optional[datetime], now: datetime) -> bool:
    """
    „Aktiv“ = begonnen & nicht beendet.
    Kleine Kulanz: Ende +10 Min bleibt sichtbar (Race-Conditions bei Updates).
    """
    if start and start > now:
        return False
    if end and end < (now - timedelta(minutes=10)):
        return False
    return True

def _norm_list(val: Any) -> str:
    """
    Linien/Stops vereinheitlichen: sortierte, kleingeschriebene Liste als String.
    """
    if not val:
        return ""
    if isinstance(val, (list, tuple)):
        items = [str(x).strip().lower() for x in val if str(x).strip()]
    else:
        items = [str(val).strip().lower()]
    return ",".join(sorted(set(items)))

def _normalize_title(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s

def _guid(*parts: str) -> str:
    base = "|".join(p or "" for p in parts)
    return hashlib.md5(base.encode("utf-8")).hexdigest()

# --- Kernlogik -----------------------------------------------------------------

def _fetch_traffic_infos(timeout: int = 20) -> Iterable[Dict[str, Any]]:
    """
    Holt restriktive Meldungen: Störungen (lang/kurz), Aufzug, Fahrtreppe.
    Quelle ist offiziell & maschinenlesbar.
    """
    params = [("name","stoerunglang"),("name","stoerungkurz"),
              ("name","aufzugsinfo"),("name","fahrtreppeninfo")]
    r = S.get(f"{BASE}/trafficInfoList", params=params, timeout=timeout)
    r.raise_for_status()
    return (r.json().get("data", {}) or {}).get("trafficInfos", []) or []

def _fetch_news(timeout: int = 20) -> Iterable[Dict[str, Any]]:
    """
    Holt allgemeine Hinweise; wir filtern anschließend nur restriktionsrelevante.
    """
    r = S.get(f"{BASE}/newsList", timeout=timeout)
    r.raise_for_status()
    return (r.json().get("data", {}) or {}).get("pois", []) or []

def fetch_events(timeout: int = 20) -> List[Dict[str, Any]]:
    """
    Rückgabe-Format je Event:
      {source, category, title, description, link, guid, pubDate(datetime)}
    - nur aktive Meldungen
    - dedupliziert (intern & gegen News)
    - nur Großraum Wien: gegeben (WL-Betriebsgebiet)
    """
    now = datetime.now(timezone.utc)

    items: List[Dict[str, Any]] = []
    seen_keys: set[str] = set()  # Cross-source-Dedup (Traffic vs. News)

    # ---- A) Traffic-Infos: immer restriktiv -----------------------------------
    try:
        for ti in _fetch_traffic_infos(timeout=timeout):
            start, end = _times(ti)
            if not _is_active(start, end, now):
                continue

            title = (ti.get("title") or ti.get("name") or "Meldung").strip()
            desc = html.escape(ti.get("description") or "")
            attrs = ti.get("attributes") or {}

            rel_lines = ti.get("relatedLines") or attrs.get("relatedLines")
            rel_stops = ti.get("relatedStops") or attrs.get("relatedStops")

            parts = []
            for k in ("status","station","location","reason","towards"):
                if attrs.get(k):
                    parts.append(f"{k.capitalize()}: {html.escape(str(attrs[k]))}")
            if rel_lines: parts.append(f"Linien: {html.escape(str(rel_lines))}")
            if rel_stops: parts.append(f"Stops: {html.escape(str(rel_stops))}")
            if parts:
                desc = (desc + ("<br/>" if desc else "") + "<br/>".join(parts))

            # Dedup-Key: Titel+Linien+Stops+Start~Stunde
            key = _guid(
                "wl-traffic",
                _normalize_title(title),
                _norm_list(rel_lines),
                _norm_list(rel_stops),
                (start or now).replace(minute=0, second=0, microsecond=0).isoformat()
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)

            items.append({
                "source": "Wiener Linien",
                "category": "Störung",
                "title": title,
                "description": desc,
                "link": "https://www.wienerlinien.at/open-data",
                "guid": key,  # stabil, kollisionsarm
                "pubDate": start or now,
            })
    except Exception as e:
        logging.exception("WL trafficInfoList fehlgeschlagen: %s", e)

    # ---- B) News/Hinweise: nur wenn restriktionsrelevant ----------------------
    try:
        for poi in _fetch_news(timeout=timeout):
            start, end = _times(poi)
            if not _is_active(start, end, now):
                continue

            title = (poi.get("title") or "Hinweis").strip()
            text_for_filter = " ".join([
                title,
                poi.get("subtitle") or "",
                poi.get("description") or "",
                str((poi.get("attributes") or {}).get("status") or "")
            ])
            if not KW_RE.search(text_for_filter):
                continue  # kein klarer Restriktionsbezug

            desc = html.escape(poi.get("description") or "")
            attrs = poi.get("attributes") or {}
            rel_lines = poi.get("relatedLines") or attrs.get("relatedLines")
            rel_stops = poi.get("relatedStops") or attrs.get("relatedStops")

            parts = []
            if poi.get("subtitle"):
                parts.append(html.escape(poi["subtitle"]))
            for k in ("station","location","towards"):
                if attrs.get(k):
                    parts.append(f"{k.capitalize()}: {html.escape(str(attrs[k]))}")
            if rel_lines: parts.append(f"Linien: {html.escape(str(rel_lines))}")
            if rel_stops: parts.append(f"Stops: {html.escape(str(rel_stops))}")
            if parts:
                desc = (desc + ("<br/>" if desc else "") + "<br/>".join(parts))

            key = _guid(
                "wl-news",
                _normalize_title(title),
                _norm_list(rel_lines),
                _norm_list(rel_stops),
                (start or now).replace(minute=0, second=0, microsecond=0).isoformat()
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)

            items.append({
                "source": "Wiener Linien",
                "category": "Hinweis",
                "title": title,
                "description": desc,
                "link": "https://www.wienerlinien.at/open-data",
                "guid": key,
                "pubDate": start or now,
            })
    except Exception as e:
        logging.exception("WL newsList fehlgeschlagen: %s", e)

    # Sortierung: neuestes oben
    items.sort(key=lambda x: x["pubDate"], reverse=True)
    return items
