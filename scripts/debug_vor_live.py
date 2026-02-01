#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Debug-Skript für VOR API (DepartureBoard).
"""

import json
import logging
import os
import sys
import requests
from typing import Optional, Dict, Any, Tuple

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
    
    base_url = os.environ.get("VOR_BASE_URL")
    access_id = os.environ.get("VOR_ACCESS_ID")
    version = os.environ.get("VOR_VERSIONS", "1.11.0")

    if not base_url or not access_id:
        logger.error("❌ FEHLER: VOR_BASE_URL oder VOR_ACCESS_ID fehlen in den Umgebungsvariablen.")
        sys.exit(1)

    # Clean base URL
    base_url = base_url.rstrip("/")

    # Construct URL for departureBoard
    # Assuming VOR_BASE_URL includes version if configured that way,
    # but strictly following the user prompt "URL: {VOR_BASE_URL}/{VOR_VERSIONS}/departureBoard"
    # we might need to be careful. In the project config, VOR_BASE_URL usually HAS the version.
    # So we simply append departureBoard.
    url = f"{base_url}/departureBoard"

    logger.info(f"Basis-Konfiguration geladen.")
    logger.info(f"URL: {url}")
    logger.info(f"AccessID: {access_id[:4]}***")

    # 2. Test DepartureBoard (Wien Hbf)
    station_id = "1292100" # Wien Hbf

    params = {
        "accessId": access_id,
        "format": "json",
        "id": station_id
    }

    logger.info(f"\n--- Starte DepartureBoard Check (Station {station_id}) ---")

    try:
        response = requests.get(url, params=params, timeout=15)
        status = response.status_code
        logger.info(f"HTTP Status: {status}")

        content = response.text
        snippet = content[:500].replace("\n", " ")
        logger.info(f"Raw Response (first 500 chars): {snippet}...")

        # Check for keywords
        found_keywords = []
        if "warning" in content.lower():
            found_keywords.append("warning")
        if "info" in content.lower():
            found_keywords.append("info")
        if "himMessage" in content:
            found_keywords.append("himMessage")

        if found_keywords:
            logger.info(f"✅ Gefundene Keywords: {', '.join(found_keywords)}")
        else:
            logger.info("ℹ️ Keine expliziten 'warning'/'info' Keywords gefunden (kann normal sein wenn keine Störung).")

        # Try parsing JSON to be sure
        try:
            data = json.loads(content)
            if "DepartureBoard" in data:
                 logger.info("✅ JSON-Struktur 'DepartureBoard' gefunden.")
                 # Check inside
                 board = data["DepartureBoard"]
                 if "warnings" in board:
                     logger.info(f"   'warnings' Feld vorhanden (Länge: {len(board['warnings'])})")
                 if "infos" in board:
                     logger.info(f"   'infos' Feld vorhanden (Länge: {len(board['infos'])})")
            elif "warnings" in data or "infos" in data:
                 logger.info("✅ JSON-Struktur mit Top-Level warnings/infos gefunden.")
            else:
                 logger.info("ℹ️ Gültiges JSON, aber erwartete Keys nicht sofort sichtbar.")
        except json.JSONDecodeError:
            logger.error("❌ Response ist kein gültiges JSON!")

    except Exception as e:
        logger.error(f"⚠️ Exception bei Request: {e}")

if __name__ == "__main__":
    main()
