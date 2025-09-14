#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
VOR / VAO Provider: Beeinträchtigungen (IMS/HIM) für S-Bahn & Regionalzüge
+ optional ÖBB-/Regionalbus (VOR_ALLOW_BUS="1").

Änderung: pubDate NUR aus Quelle (starts_at). Kein Fallback auf "jetzt".
Fehlt ein Datum, wird 'pubDate' = None geliefert; build_feed schreibt dann
KEIN <pubDate> und ordnet solche Items hinter datierten ein.
"""

from __future__ import annotations

import os, re, html, hashlib, logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any, Dict, Iterable, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from defusedxml import ElementTree as ET

try:  # pragma: no cover - support both package layouts
    from utils.ids import make_guid
except ModuleNotFoundError:  # pragma: no cover
    from src.utils.ids import make_guid  # type: ignore

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

def _get_int_env(name: str, default: int) -> int:
    val = os.getenv(name)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        log.warning("%s='%s' ist kein int – verwende %s", name, val, default)
        return default


VOR_ACCESS_ID: str | None = (os.getenv("VOR_ACCESS_ID") or os.getenv("VAO_ACCESS_ID") or "").strip() or None
VOR_STATION_IDS: List[str] = [s.strip() for s in (os.getenv("VOR_STATION_IDS") or "").split(",") if s.strip()]
VOR_BASE = os.getenv("VOR_BASE", "https://routenplaner.verkehrsauskunft.at/vao/restproxy")
VOR_VERSION = os.getenv("VOR_VERSION", "v1.3")
BOARD_DURATION_MIN = _get_int_env("VOR_BOARD_DURATION_MIN", 60)
HTTP_TIMEOUT = _get_int_env("VOR_HTTP_TIMEOUT", 15)
MAX_STATIONS_PER_RUN = _get_int_env("VOR_MAX_STATIONS_PER_RUN", 2)
ROTATION_INTERVAL_SEC = _get_int_env("VOR_ROTATION_INTERVAL_SEC", 1800)

ALLOW_BUS = (os.getenv("VOR_ALLOW_BUS", "0").strip() == "1")
BUS_INCLUDE_RE = re.compile(os.getenv("VOR_BUS_INCLUDE_REGEX", r"(?:\b[2-9]\d{2,4}\b)"))
BUS_EXCLUDE_RE = re.compile(os.getenv("VOR_BUS_EXCLUDE_REGEX", r"^(?:N?\d{1,2}[A-Z]?)$"))

RAIL_SHORT = {"S", "R", "REX", "RJ", "RJX", "IC", "EC", "EN", "D"}
RAIL_LONG_HINTS = {"S-Bahn", "Regionalzug", "Regionalexpress", "Railjet", "Railjet Express", "EuroNight"}
EXCLUDE_OPERATORS = {"Wiener Linien"}
EXCLUDE_LONG_HINTS = {"Straßenbahn", "U-Bahn"}

def _retry() -> Retry:
    return Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        raise_on_status=False,
    )


def _session() -> requests.Session:
    s = requests.Session()
    s.mount("https://", HTTPAdapter(max_retries=_retry()))
    s.headers.update({
        "Accept": "application/xml",
        "User-Agent": "Origamihase-wien-oepnv/1.2 (+https://github.com/Origamihase/wien-oepnv)",
    })
    return s

def _stationboard_url() -> str:
    return f"{VOR_BASE}/{VOR_VERSION}/DepartureBoard"

def _text(el: Optional[ET.Element], attr: str, default: str = "") -> str:
    return (el.get(attr) if el is not None else None) or default

def _parse_dt(date_str: str | None, time_str: str | None) -> Optional[datetime]:
    if not date_str: return None
    d = date_str.strip(); t = (time_str or "00:00:00").strip()
    if len(t)==5: t += ":00"
    try:
        local = datetime.fromisoformat(f"{d}T{t}").replace(tzinfo=ZoneInfo("Europe/Vienna"))
        return local.astimezone(timezone.utc)
    except Exception:
        return None

def _normalize_spaces(s: str) -> str:
    return re.sub(r"\s{2,}", " ", s).strip()

def _accept_product(prod: ET.Element) -> bool:
    catOutS = _text(prod, "catOutS").strip()
    catOutL = _text(prod, "catOutL").strip().lower()
    operator = _text(prod, "operator").strip().lower()
    line = _text(prod, "line").strip() or _text(prod, "displayNumber").strip() or _text(prod, "name").strip()
    if operator in (o.lower() for o in EXCLUDE_OPERATORS): return False
    if any(h.lower() in catOutL for h in EXCLUDE_LONG_HINTS): return False
    if catOutS.upper() == "U": return False
    if (catOutS.upper() in RAIL_SHORT) or any(h.lower() in catOutL for h in RAIL_LONG_HINTS): return True
    if not ALLOW_BUS: return False
    if BUS_EXCLUDE_RE.match(line): return False
    if BUS_INCLUDE_RE.search(line) or ("regionalbus" in catOutL) or ("postbus" in operator) or ("österreichische postbus" in operator):
        return True
    return False

def _select_stations_round_robin(ids: List[str], chunk_size: int, period_sec: int) -> List[str]:
    if not ids: return []
    m = len(ids); n = max(1, min(chunk_size, m))
    slot = int(datetime.now(timezone.utc).timestamp()) // max(1, period_sec)
    total = (m + n - 1) // n
    idx = int(slot) % total
    start = idx * n; end = start + n
    return ids[start:end] if end <= m else (ids[start:] + ids[:end-m])

def _fetch_stationboard(station_id: str, now_local: datetime) -> Optional[ET.Element]:
    params = {
        "accessId": VOR_ACCESS_ID, "format":"xml", "id": station_id,
        "date": now_local.strftime("%Y-%m-%d"), "time": now_local.strftime("%H:%M"),
        "duration": str(BOARD_DURATION_MIN), "rtMode": "SERVER_DEFAULT",
    }
    try:
        with _session() as session:
            resp = session.get(_stationboard_url(), params=params, timeout=HTTP_TIMEOUT)
        if resp.status_code >= 400:
            log.warning("VOR StationBoard %s -> HTTP %s", station_id, resp.status_code)
            return None
        return ET.fromstring(resp.content)
    except Exception as e:
        log.exception("VOR StationBoard Fehler (%s): %s", station_id, e)
        return None

def _iter_messages(root: ET.Element) -> Iterable[ET.Element]:
    parent = root.find(".//Messages")
    return [] if parent is None else list(parent.findall("./Message"))

def _accepted_products(m: ET.Element) -> List[ET.Element]:
    out: List[ET.Element] = []
    prods = m.find("./products")
    if prods is None: return out
    for p in prods.findall("./Product"):
        if _accept_product(p): out.append(p)
    return out

def _collect_from_board(station_id: str, root: ET.Element) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for m in _iter_messages(root):
        msg_id = _text(m, "id").strip()
        if not msg_id: continue
        active = _text(m, "act").strip().lower()
        if active in ("false","0","no"): continue

        prods = _accepted_products(m)
        if not prods: continue

        head = _normalize_spaces(html.escape(_text(m, "head")))
        text = _normalize_spaces(html.escape(_text(m, "text")))

        starts_at = _parse_dt(_text(m, "sDate"), _text(m, "sTime"))
        ends_at   = _parse_dt(_text(m, "eDate"), _text(m, "eTime"))

        lines: List[str] = []
        affected_stops: List[str] = []
        for p in prods:
            name = _text(p, "name") or (_text(p, "catOutS") + " " + _text(p, "displayNumber"))
            if name:
                lines.append(name.strip())
        aff = m.find("./affectedStops")
        if aff is not None:
            for st in aff.findall("./Stop"):
                nm = (st.get("name") or st.get("stop") or "").strip()
                if nm: affected_stops.append(nm)

        extras: List[str] = []
        if lines: extras.append(f"Linien: {html.escape(', '.join(sorted(set(lines))))}")
        if affected_stops: extras.append(f"Betroffene Haltestellen: {html.escape(', '.join(sorted(set(affected_stops))[:20]))}")

        description_html = text or head
        if extras: description_html += "<br/>" + "<br/>".join(extras)

        guid = make_guid("vao", msg_id)
        items.append({
            "source": "VOR/VAO",
            "category": "Störung",
            "title": head or "Meldung",
            "description": description_html,
            "link": "https://www.vor.at/",
            "guid": guid,
            "pubDate": starts_at,     # NUR Quelle (kann None sein)
            "starts_at": starts_at,
            "ends_at": ends_at,
        })
    return items

def fetch_events() -> List[Dict[str, Any]]:
    if not VOR_ACCESS_ID:
        log.info("VOR: kein VOR_ACCESS_ID gesetzt – Provider inaktiv.")
        return []
    if not VOR_STATION_IDS:
        log.info("VOR: keine VOR_STATION_IDS gesetzt – Provider inaktiv.")
        return []

    now_local = datetime.now().astimezone(ZoneInfo("Europe/Vienna"))
    station_chunk = _select_stations_round_robin(VOR_STATION_IDS, MAX_STATIONS_PER_RUN, ROTATION_INTERVAL_SEC)

    seen: set[str] = set()
    out: List[Dict[str, Any]] = []

    if not station_chunk:
        return out

    max_workers = min(MAX_STATIONS_PER_RUN, len(station_chunk)) or 1
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch_stationboard, sid, now_local): sid for sid in station_chunk}
        for fut in as_completed(futures):
            sid = futures[fut]
            try:
                root = fut.result()
            except Exception as e:  # pragma: no cover - defensive
                log.exception("VOR StationBoard Fehler (%s): %s", sid, e)
                continue
            if root is None:
                continue
            for it in _collect_from_board(sid, root):
                if it["guid"] in seen:
                    for x in out:
                        if x["guid"] == it["guid"]:
                            if it["pubDate"] and (not x["pubDate"] or it["pubDate"] < x["pubDate"]):
                                x["pubDate"] = it["pubDate"]
                            be, ee = x.get("ends_at"), it.get("ends_at")
                            x["ends_at"] = None if (be is None or ee is None) else max(be, ee)
                            if it["description"] and it["description"] not in x["description"]:
                                x["description"] += "<br/>" + it["description"]
                            break
                    continue
                seen.add(it["guid"])
                out.append(it)

    out.sort(key=lambda x: (0, x["pubDate"]) if x["pubDate"] else (1, hashlib.md5(x["guid"].encode()).hexdigest()))
    return out
