#!/usr/bin/env python3
"""Diagnose VOR/VAO authentication setup without making network calls.

This is a configuration-time sanity check: it inspects the environment and
the resulting :func:`src.providers.vor.apply_authentication` outcome and
reports whether credentials would be injected, which scheme would be used
(Bearer / Basic / accessId query parameter), and whether the configured
base URL would expose secrets over plain HTTP.

Exits with:
    0 — at least one credential mechanism is configured and consistent
    1 — base URL is plain HTTP while credentials are present (insecure)
    2 — no credentials configured
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Sequence

import requests

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.providers import vor as vor_module
from src.utils.env import load_default_env_files

LOGGER = logging.getLogger("vor.auth_check")


def _configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def _scheme_label(header: str) -> str:
    if header.startswith("Bearer "):
        return "Bearer"
    if header.startswith("Basic "):
        return "Basic"
    return "unknown"


def main(argv: Sequence[str] | None = None) -> int:
    del argv  # Unused but kept for consistency with other verify scripts.
    _configure_logging()
    load_default_env_files()

    vor_module.refresh_base_configuration()
    access_id = vor_module.refresh_access_credentials()
    auth_header = vor_module._VOR_AUTHORIZATION_HEADER

    base_url = vor_module.VOR_BASE_URL
    LOGGER.info("VOR base URL: %s", base_url)

    if not access_id and not auth_header:
        LOGGER.error(
            "No VOR credentials configured — set VOR_ACCESS_ID (or legacy VAO_ACCESS_ID)."
        )
        return 2

    if access_id:
        LOGGER.info(
            "accessId query parameter will be injected (token length: %d).",
            len(access_id),
        )
    if auth_header:
        LOGGER.info(
            "Authorization header will be sent (scheme: %s).",
            _scheme_label(auth_header),
        )

    # Build a real Session and observe what apply_authentication does to it,
    # without ever issuing a request.
    session = requests.Session()
    vor_module.apply_authentication(session)

    if session.auth is None:
        LOGGER.error(
            "apply_authentication() did not configure session.auth — credentials "
            "would not be injected on outgoing requests."
        )
        return 1

    if base_url.lower().startswith("http://"):
        LOGGER.error(
            "VOR base URL is plain HTTP (%s) while credentials are configured — "
            "secrets would leak over the wire. Refusing to report success.",
            base_url,
        )
        return 1

    LOGGER.info("VOR authentication setup looks consistent.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
