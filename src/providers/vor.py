#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Provider: VOR / VAO ReST-API – Beeinträchtigungen (IMS/HIM) für
S-Bahn & Regionalzüge (Default) plus ÖBB-/Regionalbus (wenn VOR_ALLOW_BUS="1").

Ziele:
- KEINE Dubletten mit Wiener Linien:
  * Exkludiere U-Bahn & Straßenbahn sowie WL-Bus
  * Rail (S, R/REX, RJ/RJX, IC/EC/EN/D) immer zulassen
  * ÖBB-/Regionalbus optional (VOR_ALLOW_BUS=1)
- dedupe über messageID (VAO-weit stabil)
- nur aktive Meldungen; setzt starts_at/ends_at
- Round-Robin-Auswahl der Stationen über Zeit (stateless)

ENV:
  VOR_ACCESS_ID / VAO_ACCESS_ID
  VOR_STATION_IDS                 (kommasepariert)
  VOR_BASE / VOR_VERSION
  VOR_BOARD_DURATION_MIN          (Default 60)
  VOR_HTTP_TIMEOUT                (Default 15)
  VOR_ALLOW_BUS                   ("0"|"1"; Default "0")
  VOR_BUS_INCLUDE_REGEX           (Default r"(?:\\b[2-9]\\d{2,4}\\b)")
  VOR_BUS_EXCLUDE_REGEX           (Default r"^(?:N?\\d{1,2}[A-Z]?)$")
  VOR_MAX_STATIONS_PER_RUN        (Default 2)
  VOR_ROTATION_INTERVAL_SEC       (Default 1800 = 30 min; steuert Round-Robin-Schrittweite)
"""

from __future__ import annotations

import os
import re
import html
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional
from xml.etree import ElementTree as ET

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

# ----------------------------- ENV & Defaults -----------------------------

VOR_ACCESS_ID: str | None = os.getenv("VOR_ACCESS_ID") or os.getenv("VAO_ACCESS_ID")
VOR_STATION_IDS: List[str] = [s.strip() for s in (os.getenv("VOR_STATION_IDS") or "").split(",") if s.strip()]

VOR_BASE = os.getenv("VOR_BASE", "https://routenplaner.verkehrsauskunft.at/vao/restproxy")
VOR_VERSION = os.getenv("VOR_VERSION", "v1.3")
BOARD_DURATION_MIN = int(os.getenv("VOR_BOARD_DURATION_MIN", "60"))
HTTP_TIMEOUT = int(os.getenv("VOR_HTTP_TIMEOUT", "15"))
MAX_STATIONS_PER_RUN = int(os.getenv("VOR_MAX_STATIONS_PER_RUN", "2"))
ROTATION_INTERVAL_SEC = int(os.getenv("VOR_ROTATION_INTERVAL_SEC", "1800"))  # 30 min

ALLOW_BUS = (os.getenv("VOR_ALLOW_BUS", "0").strip() == "1")
BUS_INCLUDE_RE = re.compile(os.getenv("VOR_BUS_INCLUDE_REGEX", r"(?:\b[2-9]\d{2,4}\b)"))
BUS_EXCLUDE_RE = re.compile(os.getenv("VOR_BUS_EXCLUDE_REGEX", r"^(?:N?\d{1,2}[A-Z]?)$"))

# Rail-Produktkürzel / -Namen
RAIL_SHORT = {"S", "R", "REX", "RJ", "RJX", "IC", "EC", "EN", "D"}
RAIL_LONG_HINTS = {"S-Bahn", "Regionalzug", "Regionalexpress", "Railjet", "Railjet Express", "EuroNight"}

# Dublettenvermeidung gegenüber WL
EXCLUDE_OPERATORS = {"Wiener Linien"}
EXCLUDE_LONG_HINTS = {"Straßenbahn", "U-Bahn"}

# ----------------------------- HTTP Session -----------------------------

def _session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        raise_on_status=False,
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update({
        "Accept": "application/xml",
        "User-Agent": "Origamihase-wien-oepnv/1.2 (+https://github.com/Origamihase/wien-oepnv)"
    })
    return s

S = _session()

# ----------------------------- Helpers -----------------------------

def _stationboard_url() -> str:
    return f"{VOR_BASE}/{VOR_VERSION}/DepartureBoard"

def _text(el: Optional[ET.Element], attr: str, default: str = "") -> str:
    return (el.get(attr) if el is not None else None) or default

def _parse_dt(date_str: str | None, time_str: str | None) -> Optional[datetime]:
    if not date_str:
        return None
    d = date_str.strip()
    t = (time_str or "00:00:00").strip()
    if len(t) == 5:
        t += ":00"
    try:
        return datetime.fromisoformat(f"{d}T{t}").replace(tzinfo=timezone.utc)
    except Exception:
        return None

def _guid(*parts: str) -> str:
    base = "|".join(p or "" for p in parts)
    return hashlib.md5(base.encode("utf-8")).hexdigest()

def _normalize_spaces(s: str) -> str:
    return re.sub(r"\s{2,}", " ", s).strip()

def _accept_product(prod: ET.Element) -> bool:
    """
    Entscheidet, ob ein <Product> zugelassen wird.
    - Exkludiere WL/U/Tram
    - Rail immer zulassen
    - Bus nur wenn ALLOW_BUS=1, WL-typische Linien ausschließen
    """
    catOutS = _text(prod, "catOutS").strip()
    catOutL = _text(prod, "catOutL").strip()
    operator = _text(prod, "operator").strip()
    line = _text(prod, "line").strip() or _text(prod, "displayNumber").strip() or _text(prod, "name").strip()

    if operator in EXCLUDE_OPERATORS:
        return False
    if any(h in catOutL for h in EXCLUDE_LONG_HINTS):
        return False
    if catOutS.upper() == "U":
        return False

    if (catOutS.upper() in RAIL_SHORT) or any(h in catOutL for h in RAIL_LONG_HINTS):
        return True

    if not ALLOW_BUS:
        return False

    # WL-typische Linienmuster (z. B. 13A, 26A, N60) rauskippen
    if BUS_EXCLUDE_RE.match(line):
        return False
    # Regionale/ÖBB-Busse zulassen (z. B. 400er/500er/700er, Postbus etc.)
    if BUS_INCLUDE_RE.search(line) or ("Regionalbus" in catOutL) or ("Postbus" in operator) or ("Österreichische Postbus" in operator):
        return True

    return False

def _select_stations_round_robin(ids: List[str], chunk_size: int, period_sec: int) -> List[str]:
    """
    Stateless Round-Robin über Zeit:
      - Teilt die ID-Liste in Blöcke à chunk_size.
      - Wählt je Lauf den Block anhand des 'Zeitslots' (epoch // period_sec).
    So kommen nach und nach alle Stationen dran – ohne Cursor-Datei.
    """
    if not ids:
        return []
    m = len(ids)
    n = max(1, min(chunk_size, m))
    # Index basierend auf der Anzahl vergangener Zeitfenster
    slot = int(datetime.now(timezone.utc).timestamp()) // max(1, period_sec)
    total_chunks = (m + n - 1) // n
    chunk_index = int(slot) % total_chunks
    start = chunk_index * n
    end = start + n
    if end <= m:
        return ids[start:end]
    # Wrap-around
    return ids[start:] + ids[: end - m]

# ----------------------------- Fetch/Parse -----------------------------

def _fetch_stationboard(station_id: str, now_local: datetime) -> Optional[ET.Element]:
    params = {
        "accessId": VOR_ACCESS_ID,
        "format": "xml",
        "id": station_id,
        "date": now_local.strftime("%Y-%m-%d"),
        "time": now_local.strftime("%H:%M"),
        "duration": str(BOARD_DURATION_MIN),
        "rtMode": "SERVER_DEFAULT",
    }
    try:
        resp = S.get(_stationboard_url(), params=params, timeout=HTTP_TIMEOUT)
        if resp.status_code >= 400:
            log.warning("VOR StationBoard %s -> HTTP %s", station_id, resp.status_code)
            return None
        return ET.fromstring(resp.content)
    except Exception as e:
        log.exception("VOR StationBoard Fehler (%s): %s", station_id, e)
        return None

def _iter_messages(root: ET.Element) -> Iterable[ET.Element]:
    msgs_parent = root.find(".//Messages")
    if msgs_parent is None:
        return []
    return list(msgs_parent.findall("./Message"))

def _accepted_products(m: ET.Element) -> List[ET.Element]:
    out: List[ET.Element] = []
    prods = m.find("./products")
    if prods is None:
        return out
    for p in prods.findall("./Product"):
        if _accept_product(p):
            out.append(p)
    return out

def _collect_from_board(station_id: str, root: ET.Element) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []

    for m in _iter_messages(root):
        msg_id = _text(m, "id").strip()
        if not msg_id:
            continue

        active = _text(m, "act").strip().lower()
        if active in ("false", "0", "no"):
            continue

        prods = _accepted_products(m)
        if not prods:
            continue

        head = _normalize_spaces(html.escape(_text(m, "head")))
        text = _normalize_spaces(html.escape(_text(m, "text")))

        starts_at = _parse_dt(_text(m, "sDate"), _text(m, "sTime"))
        ends_at   = _parse_dt(_text(m, "eDate"), _text(m, "eTime"))

        lines: List[str] = []
        operators: List[str] = []
        for p in prods:
            name = _text(p, "name") or (_text(p, "catOutS") + " " + _text(p, "displayNumber"))
            if name:
                lines.append(name.strip())
            op = _text(p, "operator")
            if op:
                operators.append(op.strip())

        affected_stops: List[str] = []
        aff = m.find("./affectedStops")
        if aff is not None:
            for st in aff.findall("./Stop"):
                nm = (st.get("name") or st.get("stop") or "").strip()
                if nm:
                    affected_stops.append(nm)

        extra_bits: List[str] = []
        if lines:
            extra_bits.append(f"Linien: {html.escape(', '.join(sorted(set(lines))))}")
        if affected_stops:
            extra_bits.append(f"Betroffene Haltestellen: {html.escape(', '.join(sorted(set(affected_stops))[:20]))}")

        description_html = text or head
        if extra_bits:
            description_html += "<br/>" + "<br/>".join(extra_bits)

        guid = _guid("vao", msg_id)

        items.append({
            "source": "VOR/VAO",
            "category": "Störung",
            "title": head or "Meldung",
            "description": description_html,
            "link": "https://www.vor.at/",
            "guid": guid,
            "pubDate": starts_at or datetime.now(timezone.utc),
            "starts_at": starts_at,
            "ends_at": ends_at,
        })

    return items

# ----------------------------- Public API -----------------------------

def fetch_events() -> List[Dict[str, Any]]:
    """
    Liefert Schema-kompatible Ereignisse aus VAO (Rail + optional Bus),
    mit Dublettenschutz gegenüber Wiener Linien durch Produktfilter.
    Verwendet Round-Robin, damit nach und nach alle Stationen abgefragt werden.
    """
    if not VOR_ACCESS_ID:
        log.info("VOR: kein VOR_ACCESS_ID gesetzt – Provider inaktiv.")
        return []
    if not VOR_STATION_IDS:
        log.info("VOR: keine VOR_STATION_IDS gesetzt – Provider inaktiv.")
        return []

    now_local = datetime.now(timezone.utc)

    # Round-Robin-Auswahl der Stationen (stateless, zeitbasiert)
    station_chunk = _select_stations_round_robin(VOR_STATION_IDS, MAX_STATIONS_PER_RUN, ROTATION_INTERVAL_SEC)

    seen: set[str] = set()
    out: List[Dict[str, Any]] = []

    for sid in station_chunk:
        root = _fetch_stationboard(sid, now_local)
        if root is None:
            continue
        for it in _collect_from_board(sid, root):
            if it["guid"] in seen:
                # Merge: frühestes pubDate, maximales ends_at, Beschreibung ergänzen
                for x in out:
                    if x["guid"] == it["guid"]:
                        if it["pubDate"] and x["pubDate"] and it["pubDate"] < x["pubDate"]:
                            x["pubDate"] = it["pubDate"]
                        be, ee = x.get("ends_at"), it.get("ends_at")
                        x["ends_at"] = None if (be is None or ee is None) else max(be, ee)
                        if it["description"] and it["description"] not in x["description"]:
                            x["description"] += "<br/>" + it["description"]
                        break
                continue
            seen.add(it["guid"])
            out.append(it)

    out.sort(key=lambda x: x["pubDate"], reverse=True)
    return out
