#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Debug-Skript für VOR API: Station Discovery & DepartureBoard Test.
Ziel: Ermittlung der korrekten Station-IDs (HAFAS-Format) für data/stations.json.
"""

import json
import logging
import os
import sys
import requests
from pathlib import Path

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

def main():
    # 1. Umgebungsvariablen laden
    load_default_env_files()
    
    # Default URL from src/providers/vor.py
    DEFAULT_BASE = "https://routenplaner.verkehrsauskunft.at/vao/restproxy/v1.11.0/"

    base_url = os.environ.get("VOR_BASE_URL", DEFAULT_BASE).rstrip("/") + "/"
    access_id = os.environ.get("VOR_ACCESS_ID")

    if not access_id:
        logger.error("❌ FEHLER: VOR_ACCESS_ID fehlt in den Umgebungsvariablen (oder secrets.env).")
        sys.exit(1)

    logger.info(f"Konfiguration:")
    logger.info(f"  Base URL: {base_url}")
    logger.info(f"  AccessID: {access_id[:4]}***")
    logger.info("-" * 40)

    stations_to_test = ["Wien Hauptbahnhof", "Flughafen Wien"]

    for station_name in stations_to_test:
        logger.info(f"\n🔍 Suche ID für: '{station_name}'")
        found_id = resolve_station_id(base_url, access_id, station_name)

        if found_id:
            logger.info(f"✅ Gefundene ID: {found_id}")
            logger.info(f"🚀 Teste DepartureBoard mit ID: {found_id}")
            test_departure_board(base_url, access_id, found_id)
        else:
            logger.error(f"❌ Keine ID für '{station_name}' gefunden.")

def resolve_station_id(base_url, access_id, name):
    """
    Ruft location.name auf und gibt die ID zurück.
    Druckt das JSON des ersten Treffers.
    """
    url = f"{base_url}location.name"
    params = {
        "accessId": access_id,
        "format": "json",
        "input": name,
        "type": "stop"
    }

    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()

        try:
            data = response.json()
        except json.JSONDecodeError:
            logger.error("❌ Response war kein gültiges JSON.")
            return None

        # Suche nach StopLocation
        stops = []
        if "StopLocation" in data:
            stops = data["StopLocation"]
        elif "LocationList" in data and "StopLocation" in data["LocationList"]:
             stops = data["LocationList"]["StopLocation"]

        # Manchmal ist es ein einzelnes Dict, keine Liste
        if isinstance(stops, dict):
            stops = [stops]

        if not stops:
             logger.info("ℹ️ Keine StopLocations im Response gefunden.")
             # Debug output
             logger.info(json.dumps(data, indent=2))
             return None

        first_hit = stops[0]

        # WICHTIG: Komplettes JSON für den ersten Treffer ausgeben
        logger.info("📋 JSON Response (Erster Treffer):")
        print(json.dumps(first_hit, indent=2))

        # Extrahiere ID
        # HAFAS IDs sind oft unter 'id' oder 'extId' zu finden
        station_id = first_hit.get("id")
        if not station_id:
            station_id = first_hit.get("extId")

        return station_id

    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Request Error bei location.name: {e}")
        return None

def test_departure_board(base_url, access_id, station_id):
    """
    Testet den departureBoard Endpunkt mit der gefundenen ID.
    """
    url = f"{base_url}departureBoard"
    params = {
        "accessId": access_id,
        "format": "json",
        "id": station_id
    }

    try:
        response = requests.get(url, params=params, timeout=10)
        status = response.status_code

        if status == 200:
            logger.info(f"✅ departureBoard Status 200 OK")
            # Optional: Prüfen ob Inhalt sinnvoll ist
            try:
                content = response.json()
                logger.info("   Response ist gültiges JSON.")
            except:
                logger.warning("   Response 200, aber kein valides JSON?")
        else:
            logger.error(f"❌ departureBoard fehlgeschlagen. Status: {status}")
            logger.error(f"   Response Body: {response.text[:200]}...")

    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Request Error bei departureBoard: {e}")

if __name__ == "__main__":
    main()
