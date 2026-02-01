#!/usr/bin/env python3
"""
Debug script to extract correct VOR Station IDs (Long-IDs).
"""

import sys
import os
import logging
import requests
from pathlib import Path

# Add src to path to import utils
BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

try:
    from utils.env import load_default_env_files
except ImportError:
    # Fallback if running from a different context
    sys.path.insert(0, str(BASE_DIR))
    from src.utils.env import load_default_env_files

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("debug_vor")

# Load environment variables
loaded = load_default_env_files()
if loaded:
    log.info(f"📂 Geladene Env-Dateien: {', '.join(str(p) for p in loaded.keys())}")
else:
    log.info("⚠️ Keine Env-Dateien gefunden (verwende System-Umgebungsvariablen).")

# Configuration
VOR_BASE_URL = os.getenv("VOR_BASE_URL", "https://routenplaner.verkehrsauskunft.at/vao/restproxy/v1.11.0/")
if not VOR_BASE_URL.endswith("/"):
    VOR_BASE_URL += "/"

def get_access_id():
    raw = os.getenv("VOR_ACCESS_ID") or os.getenv("VAO_ACCESS_ID") or ""
    token = raw.strip()
    if token.lower().startswith("basic "):
        token = token[6:].strip()
    return token

VOR_ACCESS_ID = get_access_id()

def fetch_station_id(search_name):
    url = f"{VOR_BASE_URL}location.name"
    params = {
        "format": "json",
        "input": search_name,
        "type": "STOP",  # Uppercase mandated
    }
    if VOR_ACCESS_ID:
        params["accessId"] = VOR_ACCESS_ID

    log.info(f"🔍 Suche nach '{search_name}'...")
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.error(f"❌ Fehler bei API-Abruf: {e}")
        return None

    # Parse Logic
    candidates = []
    if "stopLocationOrCoordLocation" in data:
         candidates = data["stopLocationOrCoordLocation"]
    elif "StopLocation" in data:
         candidates = data["StopLocation"]
         if isinstance(candidates, dict):
             candidates = [candidates]
    elif "LocationList" in data:
        loc_list = data["LocationList"]
        if "StopLocation" in loc_list:
            candidates = loc_list["StopLocation"]
            if isinstance(candidates, dict):
                candidates = [candidates]

    if not candidates:
        log.warning(f"⚠️ Keine Treffer für '{search_name}'")
        return None

    first = candidates[0]
    # Sometimes it's wrapped in StopLocation
    if "StopLocation" in first:
        first = first["StopLocation"]

    found_id = first.get("id")
    name = first.get("name")

    if found_id:
        print(f"✅ ID für '{search_name}': {found_id}")
        log.info(f"   (Name: {name})")
        return found_id
    else:
        log.warning(f"⚠️ ID nicht gefunden im ersten Treffer: {first}")
        return None

def verify_station(station_id):
    url = f"{VOR_BASE_URL}departureBoard"
    params = {
        "format": "json",
        "id": station_id
    }
    if VOR_ACCESS_ID:
        params["accessId"] = VOR_ACCESS_ID

    log.info(f"🕵️ Verifiziere ID {station_id} via DepartureBoard...")
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        # Count departures
        departures = 0

        if "DepartureBoard" in data:
            board = data["DepartureBoard"]
            if "Departure" in board:
                deps = board["Departure"]
                departures = len(deps) if isinstance(deps, list) else 1

        # Check warnings/messages
        messages = 0
        # Check various places for messages
        roots = [data]
        if "DepartureBoard" in data:
            roots.append(data["DepartureBoard"])

        for root in roots:
            for key in ["warnings", "infos", "himMessages", "trafficInfos"]:
                if key in root:
                    container = root[key]
                    if isinstance(container, list):
                        messages += len(container)
                    elif isinstance(container, dict):
                         messages += 1

        log.info(f"✅ Verifikation erfolgreich: {departures} Abfahrten, {messages} Warnungen gefunden.")
        return True
    except Exception as e:
        log.error(f"❌ Verifikation fehlgeschlagen: {e}")
        if 'resp' in locals() and resp.status_code >= 400:
             log.error(f"Response: {resp.text}")
        return False

def main():
    if not VOR_ACCESS_ID:
        log.error("❌ VOR_ACCESS_ID (oder VAO_ACCESS_ID) nicht gesetzt! Bitte .env prüfen.")
        sys.exit(1)

    # 1. Wien Hbf
    wien_hbf_id = fetch_station_id("Wien Hauptbahnhof")

    # 2. Flughafen Wien
    flughafen_id = fetch_station_id("Flughafen Wien")
    if not flughafen_id:
        log.info("↪️ Versuche Alternative 'Flughafen Wien Bahnhof'...")
        flughafen_id = fetch_station_id("Flughafen Wien Bahnhof")

    # 3. Verifikation (nur Wien Hbf wie angefordert)
    if wien_hbf_id:
        verify_station(wien_hbf_id)

    print("\n" + "="*40)
    print("ZUSAMMENFASSUNG:")
    if wien_hbf_id:
        print(f"Wien Hauptbahnhof: {wien_hbf_id}")
    if flughafen_id:
        print(f"Flughafen Wien:    {flughafen_id}")
    print("="*40)

if __name__ == "__main__":
    main()
