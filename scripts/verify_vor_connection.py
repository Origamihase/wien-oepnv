#!/usr/bin/env python3
import sys
import os
import requests
from pathlib import Path

# Add src to path to import utils
sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

try:
    from utils.env import load_default_env_files
except ImportError:
    print("Could not import utils.env. Ensure you are running from the repo root.")
    sys.exit(1)

def main():
    # Load secrets
    load_default_env_files()

    base_url = os.getenv("VOR_BASE_URL", "https://routenplaner.verkehrsauskunft.at/vao/restproxy/v1.11.0/")
    if not base_url.endswith("/"):
        base_url += "/"

    # Check for version override
    version = os.getenv("VOR_VERSION", "v1.11.0")
    # If base_url doesn't contain version, we might need to construct it,
    # but usually VOR_BASE_URL includes it.
    # For this diagnostic, we trust the env or default.

    endpoint = f"{base_url}trafficInfo"

    access_id = os.getenv("VOR_ACCESS_ID") or os.getenv("VAO_ACCESS_ID")

    if not access_id:
        print("WARNING: No VOR_ACCESS_ID found in environment.")

    print(f"Testing connection to: {endpoint}")

    # Construct params
    params = {}
    if access_id:
        params["accessId"] = access_id
        print("Using accessId from environment.")
    else:
        print("No accessId provided.")

    try:
        response = requests.get(endpoint, params=params, timeout=15)
        print(f"HTTP Status: {response.status_code}")
        print("-" * 20)
        print(f"Response Headers: {dict(response.headers)}")
        print("-" * 20)
        print("Raw Response Body (first 1000 chars):")
        print(response.text[:1000])
        print("-" * 20)

        if response.status_code == 200:
            print("SUCCESS: Connection established.")
        else:
            print("FAILURE: Non-200 status code.")

    except Exception as e:
        print(f"EXCEPTION: {e}")

if __name__ == "__main__":
    main()
