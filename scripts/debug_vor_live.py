#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
VOR API DEBUGGING SKRIPT
Zweck: Station Discovery & Verification ohne Absturz (kein 'type=stop').
"""

import json
import logging
import os
import sys
import requests

# Füge das Projektverzeichnis zum Pfad hinzu, um src zu importieren
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from src.utils.env import load_default_env_files
except ImportError:
    print("❌ Konnte src.utils.env nicht importieren. Stellen Sie sicher, dass Sie das Skript aus dem Root-Verzeichnis ausführen.")
    sys.exit(1)

# Logging Setup
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("VOR_DEBUG")

def run_debug():
    # 1. Umgebungsvariablen laden
    load_default_env_files()
    
    DEFAULT_BASE = "https://routenplaner.verkehrsauskunft.at/vao/restproxy/v1.11.0/"
    base_url = os.environ.get("VOR_BASE_URL", DEFAULT_BASE).rstrip("/") + "/"
    access_id = os.environ.get("VOR_ACCESS_ID")

    if not access_id:
        # Fallback falls anders benannt
        access_id = os.environ.get("VAO_ACCESS_ID")

    if not access_id:
        logger.error("❌ FEHLER: VOR_ACCESS_ID (oder VAO_ACCESS_ID) fehlt in den Umgebungsvariablen.")
        sys.exit(1)

    logger.info("=== VOR API STATION DISCOVERY & VERIFICATION ===")
    logger.info(f"Base URL: {base_url}")
    logger.info(f"Access ID: {access_id[:4]}***")
    logger.info("-" * 40)

    # 1. Suche nach Wien Hauptbahnhof
    wien_id = search_station(base_url, access_id, "Wien Hauptbahnhof")

    # 2. Suche nach Flughafen Wien (um ID zu finden, wie gewünscht)
    search_station(base_url, access_id, "Flughafen Wien")

    # 3. Verifikation mit der gefundenen ID von Wien Hauptbahnhof
    if wien_id:
        logger.info("-" * 40)
        verify_departure_board(base_url, access_id, wien_id)
    else:
        logger.warning("\n⚠️ Überspringe Verification, da keine ID für Wien Hauptbahnhof gefunden wurde.")

def search_station(base_url, access_id, station_name):
    url = f"{base_url}location.name"
    logger.info(f"\n🔍 SUCHE STATION: '{station_name}'")
    logger.info(f"   URL: {url}")

    # WICHTIG: KEIN 'type' Parameter senden!
    params = {
        "accessId": access_id,
        "format": "json",
        "input": station_name
    }

    try:
        response = requests.get(url, params=params, timeout=10)
        status = response.status_code
        logger.info(f"   Status: {status}")

        if status == 200:
            try:
                data = response.json()
                # JSON formatiert ausgeben
                print(json.dumps(data, indent=2, ensure_ascii=False))

                # IDs extrahieren
                found_id = None
                stops = []

                # Verschiedene Strukturen prüfen
                if "StopLocation" in data:
                    stops = data["StopLocation"]
                elif "LocationList" in data:
                     loc_list = data["LocationList"]
                     if isinstance(loc_list, dict):
                         stops = loc_list.get("StopLocation") or loc_list.get("Stop") or []

                # Falls es ein einzelnes Objekt ist, in Liste packen
                if isinstance(stops, dict):
                    stops = [stops]

                if not stops:
                    logger.warning("   ⚠️ Keine 'StopLocation' Einträge gefunden.")

                for stop in stops:
                    if not isinstance(stop, dict):
                        continue
                    name = stop.get("name", "Unbekannt")
                    sid = stop.get("id") or stop.get("extId")
                    if sid:
                        logger.info(f"✅ FOUND ID: {sid} ({name})")
                        if not found_id:
                            found_id = sid
            except json.JSONDecodeError:
                logger.error("❌ Response ist kein gültiges JSON.")
                logger.error(response.text[:500])
                return None

            return found_id

        else:
            logger.error(f"❌ Fehlerhafter Status: {status}")
            logger.error(f"   Body: {response.text}")
            return None

    except Exception as e:
        logger.error(f"❌ Exception bei Suche: {e}")
        return None

def verify_departure_board(base_url, access_id, station_id):
    url = f"{base_url}departureBoard"
    logger.info(f"\n🚀 VERIFIKATION (departureBoard) für ID: {station_id}")

    params = {
        "accessId": access_id,
        "format": "json",
        "id": station_id
    }

    try:
        response = requests.get(url, params=params, timeout=10)
        status = response.status_code
        logger.info(f"   Status: {status}")

        if status == 200:
            logger.info("✅ SUCCESS! DepartureBoard geladen.")
            # Nur kurzes Snippet zeigen
            text = response.text
            snippet = text[:500] + "..." if len(text) > 500 else text
            logger.info(f"   Response-Preview: {snippet}")
        else:
            logger.error(f"❌ Verification fehlgeschlagen. Status: {status}")
            logger.error(f"   Body: {response.text}")

    except Exception as e:
        logger.error(f"❌ Exception bei Verification: {e}")

if __name__ == "__main__":
    run_debug()
