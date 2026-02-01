import os
import requests
import json
import logging
from typing import Any, Dict, Optional

# Setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("VOR_DEBUG")

# Lade Secrets (Stelle sicher, dass diese in deiner Shell gesetzt sind!)
BASE_URL = os.environ.get("VOR_BASE_URL")
ACCESS_ID = os.environ.get("VOR_ACCESS_ID")
VERSION = os.environ.get("VOR_VERSIONS", "1.0")

if not BASE_URL or not ACCESS_ID:
    print("❌ FEHLER: VOR_BASE_URL oder VOR_ACCESS_ID fehlen in den Umgebungsvariablen.")
    exit(1)

def call_endpoint(endpoint: str, params: Dict[str, Any], description: str) -> Optional[Any]:
    url = f"{BASE_URL}/v{VERSION}{endpoint}"
    # Kopie der Params erstellen, um Seiteneffekte zu vermeiden
    request_params = params.copy()
    request_params["accessId"] = ACCESS_ID
    request_params["format"] = "json"
    
    print(f"\n--- Teste Szenario: {description} ---")
    print(f"URL: {url}")
    # Hide accessId in logs
    safe_params = {k: v for k, v in request_params.items() if k != 'accessId'}
    print(f"Params (ohne Auth): {safe_params}")
    
    try:
        r = requests.get(url, params=request_params, timeout=10)
        print(f"Status: {r.status_code}")
        
        if r.status_code == 200:
            try:
                # Versuche JSON Parsing
                data = r.json()
                print("✅ JSON Parsing erfolgreich.")
                return data
            except json.JSONDecodeError:
                # Hier der geforderte Raw-Output bei JSON-Fehler
                print(f"❌ JSON Parsing fehlgeschlagen. Raw Response Preview:\n{r.text[:500]}")
                return None
        else:
            # Auch bei Fehler-Status Raw-Output anzeigen
            print(f"❌ Status {r.status_code}. Raw Response Preview:\n{r.text[:500]}")
            return None
            
    except Exception as e:
        print(f"❌ Exception: {e}")
        return None

def test_location_name():
    # Basistest für /location.name
    data = call_endpoint("/location.name", {"input": "Wien"}, "Basistest location.name ('Wien')")
    if data:
        print(f"Response Keys: {list(data.keys())}")
        if 'stopLocationOrCoordLocation' in data:
            print(f"Gefundene Locations: {len(data['stopLocationOrCoordLocation'])}")

def test_him_search():
    # Alternative für Traffic Infos
    data = call_endpoint("/him/search", {}, "Alternative: /him/search (HIM Messages)")
    if data:
        print(f"Response Keys: {list(data.keys())}")
        # Beispielhafte Prüfung auf 'message' Key (Hafas Standard)
        if 'message' in data:
            print(f"Anzahl HIM Messages: {len(data['message'])}")

def test_departure_board():
    # Abfahrtsmonitor Wien Hbf
    data = call_endpoint("/departureBoard", {"id": "1292100"}, "Alternative: /departureBoard (Wien Hbf)")
    if data:
        print(f"Response Keys: {list(data.keys())}")
        # Prüfen auf Warnungen oder Infos im Departure Board
        warnings = data.get('warnings', [])
        infos = data.get('infos', [])
        print(f"Warnings: {len(warnings)}")
        print(f"Infos: {len(infos)}")

def test_traffic_info(name: str, params: Dict[str, Any]):
    data = call_endpoint("/trafficInfo", params, name)
    if data:
        msgs = data.get('trafficMessages', [])
        print(f"Gefundene Meldungen: {len(msgs)}")
        if len(msgs) > 0:
            print("✅ ERFOLG! Erste Meldung (Snippet):")
            print(json.dumps(msgs[0], indent=2)[:300] + "...")
            # Prüfen ob unsere Stationen dabei sind
            # Wien Hbf = 1292100, Flughafen = 1091500
            found_hbf = any("1292100" in str(m) for m in msgs)
            found_vie = any("1091500" in str(m) for m in msgs)
            print(f"Enthält Wien Hbf? {'Ja' if found_hbf else 'Nein'}")
            print(f"Enthält Flughafen? {'Ja' if found_vie else 'Nein'}")
        else:
            print("⚠️ Leere Liste zurückgegeben.")

# --- SZENARIEN ---

if __name__ == "__main__":
    # 1. Neue Tests zuerst
    test_location_name()
    test_him_search()
    test_departure_board()

    # 2. Alte Traffic Info Tests (refactored)
    print("\n=== Start Traffic Info Tests ===")

    # Alles abrufen (ohne Filter)
    test_traffic_info("Global (Kein Filter)", {})

    # Filter auf Wien Hbf (StopID direkt)
    test_traffic_info("Wien Hbf (stopId=1292100)", {"stopId": "1292100"})

    # Filter auf Wien Hbf (List Format)
    test_traffic_info("Wien Hbf (stopId[]=1292100)", {"stopId[]": "1292100"})

    # Filter mit 'name' statt ID
    test_traffic_info("Wien Hbf (name='Wien Hauptbahnhof')", {"name": "Wien Hauptbahnhof"})

    # Filter mit Wildcard Location
    test_traffic_info("Wien (name='Wien')", {"name": "Wien"})
