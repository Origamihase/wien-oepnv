#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
LOW-LEVEL DIAGNOSE SKRIPT FÜR VOR API.
Fokus: Raw Response Body bei 400 Errors, Auth-Alternativen testen.
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
logger = logging.getLogger("VOR_DIAG")

def run_diagnostics():
    # 1. Umgebungsvariablen laden
    load_default_env_files()
    
    # Defaults
    DEFAULT_BASE = "https://routenplaner.verkehrsauskunft.at/vao/restproxy/v1.11.0/"

    base_url = os.environ.get("VOR_BASE_URL", DEFAULT_BASE).rstrip("/") + "/"
    access_id = os.environ.get("VOR_ACCESS_ID")

    if not access_id:
        logger.error("❌ FEHLER: VOR_ACCESS_ID fehlt in den Umgebungsvariablen.")
        sys.exit(1)

    logger.info("=== VOR API LOW-LEVEL DIAGNOSE ===")
    logger.info(f"Base URL: {base_url}")
    logger.info(f"Access ID: {access_id[:4]}*** (Länge: {len(access_id)})")
    logger.info("-" * 40)

    target_station = "Wien Hauptbahnhof"
    endpoint_url = f"{base_url}location.name"

    # --- TEST 1: Standard Request ---
    # Hier wollen wir unbedingt den Body sehen, falls es fehlschlägt.
    logger.info("\n🔍 TEST 1: Standard Request (AccessId in URL)")
    params_std = {
        "accessId": access_id,
        "format": "json",
        "input": target_station,
        "type": "stop"
    }
    _make_request(endpoint_url, params=params_std, label="Standard")

    # --- TEST 2: No Auth ---
    # Prüft, ob der Endpunkt überhaupt reagiert (sollte 401 liefern)
    logger.info("\n🔍 TEST 2: Request OHNE Auth (Erwarte 401/403)")
    params_no_auth = {
        "format": "json",
        "input": target_station,
        "type": "stop"
    }
    _make_request(endpoint_url, params=params_no_auth, label="NoAuth")

    # --- TEST 3: Alternative Parameter-Namen ---
    logger.info("\n🔍 TEST 3: Alternative Parameter-Namen")
    # Manche APIs nutzen 'key', 'authKey' oder 'apiKey'
    alt_keys = ["key", "authKey", "apiKey", "api_key"]
    for k in alt_keys:
        logger.info(f"   Teste Parameter: ?{k}=...")
        p = params_no_auth.copy()
        p[k] = access_id
        _make_request(endpoint_url, params=p, label=f"Param-{k}")

    # --- TEST 4: Header Auth ---
    logger.info("\n🔍 TEST 4: Header Authorization")

    # A) Raw Header
    headers_raw = {"Authorization": access_id}
    _make_request(endpoint_url, params=params_no_auth, headers=headers_raw, label="Header-Raw")

    # B) Bearer Token Header
    headers_bearer = {"Authorization": f"Bearer {access_id}"}
    _make_request(endpoint_url, params=params_no_auth, headers=headers_bearer, label="Header-Bearer")

def _make_request(url, params=None, headers=None, label="Test"):
    """
    Führt Request aus und gibt Status + Body bei Fehlern aus.
    """
    try:
        # Kurzes Timeout für Diagnose
        response = requests.get(url, params=params, headers=headers, timeout=10)
        status = response.status_code

        msg = f"[{label}] Status: {status}"

        if status == 200:
            logger.info(f"✅ {msg}")
            # Prüfe ob wir validen JSON Content haben
            try:
                data = response.json()
                # Nur kurzen Ausschnitt zeigen
                logger.info("   Response ist gültiges JSON.")
            except json.JSONDecodeError:
                logger.warning("   Response 200, aber KEIN valides JSON.")
                logger.info(f"   Body Preview: {response.text[:200]}")
        else:
            logger.error(f"❌ {msg}")
            # DAS IST DER WICHTIGE TEIL: Error Body ausgeben!
            logger.error(f"   Error Body: {response.text}")

    except requests.exceptions.RequestException as e:
        logger.error(f"❌ [{label}] Exception: {e}")

if __name__ == "__main__":
    run_diagnostics()
