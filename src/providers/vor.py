#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
VOR/VAO – Board & Hinweise (IMS/HIM) – korrekter Abruf
- Auth über Query-Parameter: ``accessId=<VOR_ACCESS_ID>``
- Zusätzlich ``Authorization: Bearer``-Header ergänzen, sofern das Backend ihn
  verlangt (neuere Deployments setzen dies für REST-Zugriffe voraus).
- Endpunkte: ``.../location.name`` und ``.../DepartureBoard`` (Groß-/Kleinschreibung!)

Erforderliche Umgebungsvariablen:
  VOR_BASE_URL   z.B. https://routenplaner.verkehrsauskunft.at/vao/restproxy/v1.11.0/
  VOR_ACCESS_ID  z.B. d53648ac-12fe-781d-ba4a-ec9b2a0d891a

Optionale Umgebungsvariablen:
  VOR_STATION_IDS      Kommagetrennte Liste von Station-IDs (z.B. 4308484800,4308488400)
  VOR_STATION_NAMES    Kommagetrennte Namen, die per location.name auf IDs aufgelöst werden
  VOR_DURATION_MIN     Minuten für das Board (Default 60)
  VOR_ALLOW_BUS        "1" um zusätzlich Bus aufzunehmen (Default 0)
"""

from __future__ import annotations
import os
import sys
import time
import json
import requests
from typing import List, Dict, Any, Optional
from datetime import datetime
from zoneinfo import ZoneInfo

UA = "VOR-Checker/1.0 (+github-actions)"
TIMEOUT = 20

def _require_env(name: str) -> str:
    val = (os.getenv(name) or "").strip()
    if not val:
        print(f"Fehlende Variable: {name}", file=sys.stderr)
        sys.exit(2)
    return val

BASE = _require_env("VOR_BASE_URL").rstrip("/") + "/"
ACCESS_ID = _require_env("VOR_ACCESS_ID")

DURATION = int(os.getenv("VOR_DURATION_MIN") or "60")
ALLOW_BUS = (os.getenv("VOR_ALLOW_BUS") or "").strip() in ("1", "true", "True")

def _bitmask(classes: List[int]) -> int:
    m = 0
    for c in classes:
        if c >= 0:
            m |= 1 << c
    return m

def desired_product_classes() -> List[int]:
    rail = [0, 1, 2, 3, 4]      # Bahn: S, R/REX, IC/EC/RJ/RJX/EN/D
    bus  = [7]                  # Regionalbus (optional)
    return rail + (bus if ALLOW_BUS else [])

def get_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"Accept": "application/json", "User-Agent": UA})
    # Neuere VAO-Backends verlangen zusätzlich einen Bearer-Token im Header.
    s.headers.setdefault("Authorization", f"Bearer {ACCESS_ID}")
    return s

def get_json(session: requests.Session, endpoint: str, params: Dict[str, str]) -> Dict[str, Any]:
    url = BASE + endpoint
    q = dict(params)
    q["accessId"] = ACCESS_ID
    q.setdefault("format", "json")
    resp = session.get(url, params=q, timeout=TIMEOUT)
    if resp.status_code != 200:
        raise RuntimeError(f"{endpoint} -> HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.json()

def resolve_station_ids_by_name(session: requests.Session, names_csv: str) -> List[str]:
    out: List[str] = []
    for raw in names_csv.split(","):
        name = raw.strip()
        if not name:
            continue
        data = get_json(session, "location.name", {"input": name, "type": "stop"})
        # API liefert je nach Version unterschiedliche Container; beide Varianten unterstützen
        stops = []
        if isinstance(data, dict):
            stops = data.get("StopLocation") or data.get("LocationList", {}).get("Stop")
        if isinstance(stops, dict):
            stops = [stops]
        if not isinstance(stops, list) or not stops:
            print(f"location.name '{name}': keine StopLocation gefunden", file=sys.stderr)
            continue
        sid = str(stops[0].get("id") or stops[0].get("extId") or "").strip()
        if not sid:
            print(f"location.name '{name}': keine ID in erster StopLocation", file=sys.stderr)
            continue
        out.append(sid)
    return out

def fetch_board(session: requests.Session, station_id: str) -> Dict[str, Any]:
    now = datetime.now(ZoneInfo("Europe/Vienna"))
    params = {
        "id": station_id,
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "duration": str(DURATION),
        "products": str(_bitmask(desired_product_classes())),
        "rtMode": "SERVER_DEFAULT",
        "requestId": f"sb-{station_id}-{int(time.time())}",
    }
    return get_json(session, "DepartureBoard", params)

def extract_messages(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    # Messages können top-level im Board-Objekt liegen; Container-Namen variieren
    board = payload.get("DepartureBoard") if isinstance(payload, dict) else None
    node = board if isinstance(board, dict) else payload
    messages = (node.get("Messages") if isinstance(node, dict) else None) or node.get("Message")
    # Normalize to list of dicts
    if isinstance(messages, dict):
        messages = [messages]
    if not isinstance(messages, list):
        return []
    out: List[Dict[str, Any]] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        if str(m.get("act", "true")).lower() in ("0", "false", "no"):
            continue
        out.append({
            "id": str(m.get("id") or ""),
            "head": str(m.get("head") or "").strip(),
            "text": str(m.get("text") or "").strip(),
            "sDate": str(m.get("sDate") or ""),
            "sTime": str(m.get("sTime") or ""),
            "eDate": str(m.get("eDate") or ""),
            "eTime": str(m.get("eTime") or ""),
        })
    return out

def main() -> None:
    session = get_session()

    ids_env = (os.getenv("VOR_STATION_IDS") or "").strip()
    names_env = (os.getenv("VOR_STATION_NAMES") or "").strip()

    station_ids: List[str] = [s.strip() for s in ids_env.split(",") if s.strip()]
    if not station_ids and names_env:
        station_ids = resolve_station_ids_by_name(session, names_env)

    if not station_ids:
        print("Keine Stationen angegeben. Setze VOR_STATION_IDS oder VOR_STATION_NAMES.", file=sys.stderr)
        sys.exit(3)

    for sid in station_ids:
        payload = fetch_board(session, sid)
        msgs = extract_messages(payload)
        print(json.dumps({
            "station_id": sid,
            "messages_count": len(msgs),
            "messages": msgs
        }, ensure_ascii=False))

if __name__ == "__main__":
    main()
