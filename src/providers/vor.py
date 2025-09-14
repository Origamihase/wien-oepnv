#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Provider: VOR / VAO ReST-API (IMS/HIM-Meldungen über StationBoard)

- Zielt auf "Beeinträchtigungen" (Ersatzverkehr, Baustelle, Ausfall, Notfall, Vorankündigung)
- Nutzt die in StationBoard/JourneyDetails mitgelieferten <Messages>-Blöcke
- Regionseingrenzung über eine kleine Menge Wiener Stationen (ENV: VOR_STATION_IDS)
- Deduping über messageID (VAO/MVO-weit stabil)
- Setzt starts_at / ends_at, und liefert Schema-kompatible Items für build_feed.py

Hinweis zu Limits:
- VAO Start: ~100 Abfragen/Tag. Bei 30-Minuten-Run => max. 2 Stationen pro Lauf (~96/Tag).
- Darum: halte VOR_STATION_IDS bewusst klein (z. B. Hauptbahnhof + Praterstern).
"""

from __future__ import annotations

import os
import logging
import html
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple
from xml.etree import ElementTree as ET

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

# ----------------------------- Konfiguration über ENV -----------------------------

VOR_ACCESS_ID: str | None = os.getenv("VOR_ACCESS_ID") or os.getenv("VAO_ACCESS_ID")
# Komma-separierte Liste von Station-IDs (stopExtId / id aus VAO), z. B.: "490101200,490102000"
VOR_STATION_IDS: List[str] = [s.strip() for s in (os.getenv("VOR_STATION_IDS") or "").split(",") if s.strip()]

# Basis-URL & Version
VOR_BASE = os.getenv("VOR_BASE", "https://routenplaner.verkehrsauskunft.at/vao/restproxy")
VOR_VERSION = os.getenv("VOR_VERSION", "v1.3")

# Abfragefenster: wir brauchen <Messages>, die hängen am Board ohnehin global an.
# Eine Stunde reicht und ist schlank.
BOARD_DURATION_MIN = int(os.getenv("VOR_BOARD_DURATION_MIN", "60"))

# HTTP Timeout
HTTP_TIMEOUT = int(os.getenv("VOR_HTTP_TIMEOUT", "15"))

# Erlaubte HIM-Kategorien (nur echte Beeinträchtigungen):
# 0=Ersatzverkehr, 1=Baustelle, 2=Ausfall, 5=Notfall, 9=Vorankündigung
ALLOWED_HIM_CATEGORIES = {0, 1, 2, 5, 9}

# Kategorienamen für Ausgabe
HIM_TO_CATEGORY = {
    0: "Ersatzverkehr",
    1: "Baustelle",
    2: "Ausfall",
    5: "Notfall",
    9: "Vorankündigung",
}


# -------------------------------- HTTP/Retry Helper --------------------------------

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
        "User-Agent": "Origamihase-wien-oepnv/1.0 (+https://github.com/Origamihase/wien-oepnv)"
    })
    return s

S = _session()


# -------------------------------- Zeit/Helfer --------------------------------

def _parse_dt(date_str: Optional[str], time_str: Optional[str]) -> Optional[datetime]:
    """VAO gibt oft sDate/eDate (YYYY-MM-DD) und sTime/eTime (HH:MM:SS)."""
    if not date_str:
        return None
    d = date_str.strip()
    t = (time_str or "00:00:00").strip()
    try:
        # Times ohne Sekunden tolerieren
        if len(t) == 5:
            t = t + ":00"
        dt = datetime.fromisoformat(f"{d}T{t}")
        # VAO-Zeit ist lokal (AT); wir nehmen Europe/Vienna als naive Annahme
        # und setzen UTC an – build_feed formatiert ohnehin nach Vienna.
        return dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _fmt_lines(stops_or_lines: List[str], cap: int = 15) -> str:
    if not stops_or_lines:
        return ""
    arr = sorted({s for s in (x.strip() for x in stops_or_lines) if s})
    text = ", ".join(arr[:cap])
    return text + (" …" if len(arr) > cap else "")


def _guid(*parts: str) -> str:
    base = "|".join(p or "" for p in parts)
    return hashlib.md5(base.encode("utf-8")).hexdigest()


# ------------------------------ VAO StationBoard --------------------------------

def _stationboard_url() -> str:
    return f"{VOR_BASE}/{VOR_VERSION}/DepartureBoard"


def _fetch_stationboard(station_id: str, now_local: datetime) -> Optional[ET.Element]:
    """Holt eine StationBoard-Response (XML) für eine Station-ID."""
    params = {
        "accessId": VOR_ACCESS_ID,
        "format": "xml",
        "id": station_id,
        "date": now_local.strftime("%Y-%m-%d"),
        "time": now_local.strftime("%H:%M"),
        "duration": str(BOARD_DURATION_MIN),
        "rtMode": "SERVER_DEFAULT",
        # 'type': 'DEP_STATION'  # Standard ist ok; Messages hängen global an <DepartureBoard>
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
    """Liefert alle <Message>-Elemente (falls vorhanden)."""
    # Je nach Version: <DepartureBoard><Messages><Message .../></Messages></DepartureBoard>
    msgs_parent = root.find(".//Messages")
    if msgs_parent is None:
        return []
    return list(msgs_parent.findall("./Message"))


def _text(el: Optional[ET.Element], attr: str, default: str = "") -> str:
    return (el.get(attr) if el is not None else None) or default


def _collect_from_board(station_id: string, root: ET.Element) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []

    for m in _iter_messages(root):
        msg_id = _text(m, "id").strip()
        if not msg_id:
            # manche Messages ohne ID ignorieren
            continue

        active = _text(m, "act", "").strip().lower()
        if active in ("false", "0", "no"):
            # inaktive Meldungen ignorieren
            continue

        # Kategorie mappen & filtern
        cat_raw = _text(m, "category", "").strip()
        try:
            cat_code = int(cat_raw)
        except Exception:
            # Einige Nachrichten können Kategorien als Text tragen – ignorieren
            continue
        if cat_code not in ALLOWED_HIM_CATEGORIES:
            continue
        category_name = HIM_TO_CATEGORY.get(cat_code, "Hinweis")

        head = html.escape(_text(m, "head", "").strip())
        text = html.escape(_text(m, "text", "").strip())

        # Zeitfenster
        sDate = _text(m, "sDate", "").strip()
        sTime = _text(m, "sTime", "").strip()
        eDate = _text(m, "eDate", "").strip()
        eTime = _text(m, "eTime", "").strip()

        starts_at = _parse_dt(sDate, sTime)
        ends_at   = _parse_dt(eDate, eTime)

        # AffectedStops (optional, zur Info)
        affected_stops: List[str] = []
        aff = m.find("./affectedStops")
        if aff is not None:
            for st in aff.findall("./Stop"):
                nm = (st.get("name") or st.get("stop") or "").strip()
                if nm:
                    affected_stops.append(nm)

        # Products/Lines (optional)
        products: List[str] = []
        prods = m.find("./products")
        if prods is not None:
            for p in prods.findall("./Product"):
                nm = (p.get("name") or p.get("catOutL") or p.get("catOutS") or p.get("line") or "").strip()
                if nm:
                    products.append(nm)

        # Beschreibung zusammenbauen (HTML – build_feed wandelt später in Klartext)
        desc_parts: List[str] = []
        if text:
            desc_parts.append(text)
        extras: List[str] = []
        if products:
            extras.append(f"Linien: {html.escape(_fmt_lines(products))}")
        if affected_stops:
            extras.append(f"Betroffene Haltestellen: {html.escape(_fmt_lines(affected_stops))}")
        if extras:
            desc_parts.append("<br/>" + "<br/>".join(extras))
        description_html = "".join(desc_parts) if desc_parts else head

        guid = _guid("vao", str(cat_code), msg_id)

        items.append({
            "source": "VOR/VAO",
            "category": category_name,
            "title": head or category_name,
            "description": description_html,
            "link": "https://www.vor.at/",
            "guid": guid,
            "pubDate": starts_at or datetime.now(timezone.utc),
            "starts_at": starts_at,
            "ends_at": ends_at,
        })

    return items


# --------------------------------- Public API ---------------------------------

def fetch_events() -> List[Dict[str, Any]]:
    """
    Liefert eine Liste Schema-kompatibler Ereignisse aus VAO (nur Beeinträchtigungen).
    Gibt [] zurück, wenn:
      - kein ACCESS_ID, oder
      - keine Station-IDs gesetzt, oder
      - API nicht erreichbar/keine Messages.
    """
    if not VOR_ACCESS_ID:
        log.info("VOR: kein VOR_ACCESS_ID gesetzt – Provider inaktiv.")
        return []

    if not VOR_STATION_IDS:
        log.info("VOR: keine VOR_STATION_IDS gesetzt – Provider inaktiv.")
        return []

    # Zeit in Vienna (für date/time-Parameter)
    now_local = datetime.now(timezone.utc)  # Build-Feed formatiert später nach Vienna

    # Sammeln & deduplizieren nach messageID
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []

    for sid in VOR_STATION_IDS[:2]:  # Rate-Schutz: nur 2 Stationen/Lauf
        root = _fetch_stationboard(sid, now_local)
        if root is None:
            continue
        for it in _collect_from_board(sid, root):
            # Dedupe über GUID (basiert auf VAO messageID + Kategorie)
            if it["guid"] in seen:
                # pubDate minimal halten (frühestes Startdatum bevorzugen)
                for x in out:
                    if x["guid"] == it["guid"]:
                        if it["pubDate"] and x["pubDate"] and it["pubDate"] < x["pubDate"]:
                            x["pubDate"] = it["pubDate"]
                        # ends_at zusammenführen: wenn einer None -> offen; sonst max()
                        be, ee = x.get("ends_at"), it.get("ends_at")
                        x["ends_at"] = None if (be is None or ee is None) else max(be, ee)
                        # Beschreibung mergen (betroffene Stops/Linien)
                        if it["description"] and it["description"] not in x["description"]:
                            x["description"] += "<br/>" + it["description"]
                        break
                continue
            seen.add(it["guid"])
            out.append(it)

    # Sortierung: neueste zuerst
    out.sort(key=lambda x: x["pubDate"], reverse=True)
    return out
