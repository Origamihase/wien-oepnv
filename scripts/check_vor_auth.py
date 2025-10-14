#!/usr/bin/env python3
"""Check VOR/VAO API authentication.

This helper script performs a single request against the configured
VOR/VAO REST endpoint using the credentials that the application would
use.  The response body is analysed for authentication errors so that a
failing credential configuration can be identified quickly without
consuming multiple daily requests.

The script prints a JSON document to stdout that contains the
request URL, HTTP status code, detected error information and a final
``authenticated`` flag.  A non-zero exit code is returned when
authentication fails so that the script can be used inside CI jobs.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

import requests

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:  # pragma: no cover - support both package layouts
    from utils.http import session_with_retries
    from providers import vor
except ModuleNotFoundError:  # pragma: no cover
    from src.utils.http import session_with_retries  # type: ignore
    from src.providers import vor  # type: ignore


AUTH_ERROR_CODES = {"API_AUTH", "HCI_AUTH", "HAFAS_AUTH"}
AUTH_ERROR_PREFIXES = ("access denied", "invalid authorization")
DEFAULT_STATION_ID = "430470800"


def _parse_payload(response: requests.Response) -> Dict[str, Any]:
    """Return the parsed response payload.

    The VOR API sometimes returns JSON objects with error metadata.  If
    the content type is XML or the body cannot be parsed as JSON we
    return an empty dictionary instead of raising an exception so that
    the caller can continue inspecting the HTTP status code.
    """

    content_type = response.headers.get("Content-Type", "").lower()
    if "json" not in content_type:
        try:
            response.raise_for_status()
        except requests.HTTPError:
            return {"raw": response.text.strip()}
        return {}

    try:
        parsed = response.json()
    except ValueError:
        return {"raw": response.text.strip()}

    if isinstance(parsed, dict):
        return parsed
    return {"raw": response.text.strip()}


def check_authentication(station_id: str | None = None) -> Dict[str, Any]:
    """Perform a single StationBoard request and analyse the result."""

    sid = (station_id or os.getenv("VOR_AUTH_TEST_STATION") or DEFAULT_STATION_ID).strip()
    params: Dict[str, Any] = {"format": "json", "id": sid}
    if vor.VOR_ACCESS_ID:
        params["accessId"] = vor.VOR_ACCESS_ID

    url = f"{vor.VOR_BASE_URL}departureboard"

    with session_with_retries(vor.VOR_USER_AGENT, **vor.VOR_RETRY_OPTIONS) as session:
        session.headers.update(vor.VOR_SESSION_HEADERS)
        response = session.get(url, params=params, timeout=vor.HTTP_TIMEOUT)

    payload = _parse_payload(response)
    error_code = str(payload.get("errorCode") or "").strip() if isinstance(payload, dict) else ""
    error_text = str(payload.get("errorText") or "").strip() if isinstance(payload, dict) else ""

    authenticated = response.status_code < 400
    if error_code:
        authenticated = authenticated and error_code not in AUTH_ERROR_CODES
    if error_text:
        lowered = error_text.lower()
        if any(lowered.startswith(prefix) for prefix in AUTH_ERROR_PREFIXES):
            authenticated = False

    return {
        "url": response.url,
        "status_code": response.status_code,
        "error_code": error_code or None,
        "error_text": error_text or None,
        "authenticated": authenticated,
        "payload": payload,
    }


def main(argv: list[str]) -> int:
    result = check_authentication()
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0 if result.get("authenticated") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv))
