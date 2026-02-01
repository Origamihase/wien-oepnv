#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Systematische Suche nach dem korrekten VOR API Endpunkt.
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
    version = os.environ.get("VOR_VERSIONS", "1.0")

    if not base_url or not access_id:
        logger.error("❌ FEHLER: VOR_BASE_URL oder VOR_ACCESS_ID fehlen in den Umgebungsvariablen.")
        sys.exit(1)

    base_url = base_url.rstrip("/")
    logger.info(f"Basis-Konfiguration geladen.")
    logger.info(f"URL: {base_url}")
    logger.info(f"Version: {version}")
    logger.info(f"AccessID: {access_id[:4]}***")

    # 2. Zu testende Patterns definieren
    patterns = [
        f"{base_url}/trafficInfo",              # Ohne Version
        f"{base_url}/v{version}/trafficInfo",   # Mit Version (Standard)
        f"{base_url}/otp/trafficInfo",          # OpenTripPlanner Style
        f"{base_url}/him/search",               # HAFAS HIM Search
        f"{base_url}/v{version}/himsearch",     # VOR v1.11.0 Standard (himsearch ohne Slash)
    ]

    working_endpoint = None

    logger.info("\n--- Starte URL-Pattern Tests ---")

    for url in patterns:
        success, content_snippet = test_endpoint(url, access_id)
        if success:
            working_endpoint = url
            # Wenn wir trafficInfo gefunden haben, bevorzugen wir das und brechen vielleicht ab?
            # Der User will systematisch suchen. Wir merken uns den letzten funktionierenden oder brechen beim ersten ab.
            # Nehmen wir den ersten Treffer.
            break

    # 4. Stations-Check (Optional)
    if working_endpoint:
        logger.info(f"\n✅ Working Endpoint found: {working_endpoint}")

        # Check ob es trafficInfo oder him ist für den Parameternamen
        if "trafficInfo" in working_endpoint:
            logger.info("\n--- Starte Stations-Check (Wien Hbf) ---")
            check_station(working_endpoint, access_id)
        elif "him" in working_endpoint:
             logger.info("\n--- Starte Stations-Check (Wien Hbf via HIM) ---")
             # HIM search hat evtl andere Parameter, aber wir probieren es mal generisch
             check_station(working_endpoint, access_id)
    else:
        logger.error("\n❌ Kein funktionierender Endpunkt gefunden.")
        sys.exit(1)

def test_endpoint(url: str, access_id: str) -> Tuple[bool, Optional[str]]:
    """Testet einen Endpunkt und gibt (Erfolg, Snippet) zurück."""
    params = {
        "accessId": access_id,
        "format": "json"
    }

    # Maskierte URL für Log
    log_url = url.replace(access_id, "***")
    logger.info(f"Teste: {log_url}")

    try:
        response = requests.get(url, params=params, timeout=10)
        status = response.status_code
        content = response.text.strip()

        start_char = content[0] if content else ""

        if start_char in ("{", "["):
            logger.info(f"✅ TREFFER! (Status {status})")
            snippet = content[:200].replace("\n", " ")
            logger.info(f"   Response: {snippet}...")
            return True, content
        elif start_char == "<":
            logger.info(f"❌ Falscher Endpunkt (HTML/XML, Status {status})")
            return False, None
        else:
            logger.info(f"❓ Unbekannter Content (Start: '{start_char}', Status {status})")
            return False, None

    except Exception as e:
        logger.error(f"⚠️ Exception bei Request: {e}")
        return False, None

def check_station(url: str, access_id: str):
    """Testet eine spezifische Station am gefundenen Endpunkt."""
    # Wien Hbf = 1292100
    params = {
        "accessId": access_id,
        "format": "json",
        "stopId": "1292100"
    }

    logger.info(f"Request mit stopId=1292100...")
    try:
        response = requests.get(url, params=params, timeout=10)
        content = response.text.strip()
        if content.startswith("{") or content.startswith("["):
            logger.info("✅ Station-Response ist JSON.")
            logger.info(f"   Response: {content[:200]}...")
        else:
            logger.warning("⚠️ Station-Response ist KEIN JSON (trotz funktionierendem Endpunkt).")
            logger.info(f"   Start: {content[:50]}...")
    except Exception as e:
        logger.error(f"⚠️ Fehler beim Station-Check: {e}")

if __name__ == "__main__":
    main()
