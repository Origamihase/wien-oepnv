#!/usr/bin/env python3
"""
Debug script to extract and verify VOR Station IDs (Hafas Long-IDs).
"""

import sys
import os
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

# Load environment variables
load_default_env_files()

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

def get_station_id(search_term):
    url = f"{VOR_BASE_URL}location.name"
    params = {
        "format": "json",
        "input": search_term,
        # "type": "STOP"  <-- REMOVED per requirements to fix 400 errors
    }
    if VOR_ACCESS_ID:
        params["accessId"] = VOR_ACCESS_ID

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"❌ Error fetching '{search_term}': {e}")
        return None

    # Parse Logic
    candidates = []
    if "stopLocationOrCoordLocation" in data:
         candidates = data["stopLocationOrCoordLocation"]
    elif "StopLocation" in data:
         val = data["StopLocation"]
         candidates = val if isinstance(val, list) else [val]

    found_id = None
    found_name = None

    for item in candidates:
        # Handle wrapper structure: { "StopLocation": { ... } } vs { ... }
        stop_loc = item.get("StopLocation", item)

        sid = stop_loc.get("id")
        name = stop_loc.get("name")

        if sid:
            found_id = sid
            found_name = name
            break

    if found_id:
        print(f"✅ Gefunden: {found_name} -> {found_id}")
        return found_id
    else:
        print(f"⚠️ Nichts gefunden für: {search_term}")
        return None

def main():
    print(f"Running VOR Debug Script against {VOR_BASE_URL}")

    if not VOR_ACCESS_ID:
        print("❌ VOR_ACCESS_ID not found in environment. API calls may fail.")

    # 1. Search for Wien Hauptbahnhof
    print("\n--- Suche 1: Wien Hauptbahnhof ---")
    get_station_id("Wien Hauptbahnhof")

    # 2. Search for Flughafen Wien Bahnhof
    print("\n--- Suche 2: Flughafen Wien Bahnhof ---")
    airport_id = get_station_id("Flughafen Wien Bahnhof")

    # 3. Verification
    if airport_id:
        print(f"\n--- Verifikation (DepartureBoard) für ID: {airport_id} ---")
        url = f"{VOR_BASE_URL}departureBoard"
        params = {
            "format": "json",
            "id": airport_id
        }
        if VOR_ACCESS_ID:
            params["accessId"] = VOR_ACCESS_ID

        try:
            resp = requests.get(url, params=params, timeout=10)
            print(f"HTTP Status: {resp.status_code}")
            print(f"Response (first 200 chars): {resp.text[:200]}")
        except Exception as e:
            print(f"❌ Verification request failed: {e}")

if __name__ == "__main__":
    main()
