import os
import requests
import json
import logging

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

def test_endpoint(name, params):
    url = f"{BASE_URL}/v{VERSION}/trafficInfo"
    params["accessId"] = ACCESS_ID
    params["format"] = "json"
    
    print(f"\n--- Teste Szenario: {name} ---")
    print(f"URL: {url}")
    print(f"Params (ohne Auth): { {k:v for k,v in params.items() if k != 'accessId'} }")
    
    try:
        r = requests.get(url, params=params, timeout=10)
        print(f"Status: {r.status_code}")
        
        if r.status_code == 200:
            data = r.json()
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
        else:
            print(f"❌ Fehler Response: {r.text[:200]}")
            
    except Exception as e:
        print(f"❌ Exception: {e}")

# --- SZENARIEN ---

# 1. Alles abrufen (ohne Filter) - Achtung: Kann viele Daten sein
test_endpoint("Global (Kein Filter)", {})

# 2. Filter auf Wien Hbf (StopID direkt)
test_endpoint("Wien Hbf (stopId=1292100)", {"stopId": "1292100"})

# 3. Filter auf Wien Hbf (List Format)
test_endpoint("Wien Hbf (stopId[]=1292100)", {"stopId[]": "1292100"})

# 4. Filter mit 'name' statt ID
test_endpoint("Wien Hbf (name='Wien Hauptbahnhof')", {"name": "Wien Hauptbahnhof"})

# 5. Filter mit Wildcard Location (falls die API das unterstützt)
test_endpoint("Wien (name='Wien')", {"name": "Wien"})
