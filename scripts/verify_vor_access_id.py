#!/usr/bin/env python3
"""Verify that the configured VOR/VAO access credentials authorize a request.

Performs a single ``location.name`` lookup ("Wien Hauptbahnhof") against the
configured VOR base URL using the credentials that ``src.providers.vor``
loads from the environment. Exits with:

    0 — credentials accepted, response indicates a known stop
    1 — request failed (HTTP error, network issue, or unexpected payload)
    2 — no credentials configured (``VOR_ACCESS_ID`` / ``VAO_ACCESS_ID``)
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from collections.abc import Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.providers import vor as vor_module
from src.utils.env import load_default_env_files
from src.utils.http import fetch_content_safe, session_with_retries

LOGGER = logging.getLogger("vor.verify")

PROBE_QUERY = "Wien Hauptbahnhof"


def _configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def _build_probe_url() -> str:
    base = vor_module.VOR_BASE_URL.rstrip("/")
    return f"{base}/location.name"


def _looks_like_stop(payload: object) -> bool:
    """Detect a successful VOR ``location.name`` payload.

    The endpoint typically returns ``{"stopLocationOrCoordLocation": [...]}``;
    older responses use ``{"LocationList": {"StopLocation": [...]}}``. We accept
    either shape as long as it contains at least one entry.
    """
    if not isinstance(payload, dict):
        return False
    primary = payload.get("stopLocationOrCoordLocation")
    if isinstance(primary, list) and primary:
        return True
    legacy = payload.get("LocationList")
    if isinstance(legacy, dict):
        stops = legacy.get("StopLocation")
        if isinstance(stops, list) and stops:
            return True
    return False


def main(argv: Sequence[str] | None = None) -> int:
    del argv  # Unused but kept for consistency with other verify scripts.
    _configure_logging()
    load_default_env_files()

    vor_module.refresh_base_configuration()
    access_id = vor_module.refresh_access_credentials()

    if not access_id and not vor_module._VOR_AUTHORIZATION_HEADER:
        LOGGER.error(
            "VOR_ACCESS_ID (or VAO_ACCESS_ID) is not set — cannot verify access."
        )
        return 2

    probe_url = _build_probe_url()
    params = {"input": PROBE_QUERY, "format": "json"}

    LOGGER.info("Probing %s with input=%r", probe_url, PROBE_QUERY)

    try:
        with session_with_retries(vor_module.VOR_USER_AGENT) as session:
            vor_module.apply_authentication(session)
            content = fetch_content_safe(
                session,
                probe_url,
                params=params,
                timeout=vor_module.DEFAULT_HTTP_TIMEOUT,
                allowed_content_types=("application/json", "text/json"),
            )
    except Exception as exc:
        LOGGER.error("VOR verification request failed: %s", exc)
        return 1

    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        LOGGER.error("VOR response was not valid JSON: %s", exc)
        return 1

    if not _looks_like_stop(payload):
        LOGGER.error(
            "VOR response did not contain a recognised stop list — credentials "
            "may be valid but the API contract has changed."
        )
        return 1

    LOGGER.info("VOR credentials accepted; %r resolved successfully.", PROBE_QUERY)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
