#!/usr/bin/env python3
"""Smoke-test the OpenStreetMap Overpass API before kicking off enrichment.

The Vienna station-directory enrichment pipeline pivots on Overpass as the
primary source. When the public mirror is degraded a full enrichment run can
spend up to ~108 seconds per request inside the urllib3 retry stack before
giving up — multiply by every Vienna stop-area and the cron tick easily blows
past the 60-minute job ceiling without producing any new data.

This helper performs a single low-cost probe against the trusted Overpass
endpoint (or the project-pinned mirror) and returns within a tight timeout.
It is meant to be wired into CI as a fail-fast / skip-OSM gate so the cron
pipeline either bails immediately or transparently degrades to the Google
Places fallback rather than waiting on stalled connections.

Exit codes:
    0 — Overpass is reachable; OSM enrichment is safe to run.
    1 — Overpass returned a non-2xx status / unexpected payload.
    2 — Network / DNS / connect-timeout failure; treat the mirror as down.

Behaviour notes:
    * No OSM data is downloaded — the probe issues an Overpass ``out count``
      query that returns a single counter element. The trip stays well under
      the 1 KiB response cap.
    * The endpoint is resolved via :func:`get_overpass_endpoint` so the
      project's allow-list contract is honoured (env override or default).
    * The ``--allow-skip`` flag downgrades exit code 2 (network failure) to
      a successful exit so a workflow can keep going while marking the OSM
      step ``continue-on-error``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from collections.abc import Sequence

import requests

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.feed.logging_safe import setup_script_logging
from src.places.osm_client import (
    DEFAULT_OVERPASS_ENDPOINTS,
    get_overpass_endpoint,
)
from src.utils.http import request_safe, session_with_retries
from src.utils.logging import sanitize_log_arg

LOGGER = logging.getLogger("places.osm.smoke")

_USER_AGENT = "wien-oepnv-overpass-smoke/1.0 " "(+https://github.com/Origamihase/wien-oepnv; ci-preflight)"

# Overpass QL probe — returns a single counter element. Sized in bytes so the
# upstream operator's fair-use policy is respected even on degraded runners.
_PROBE_QUERY = "[out:json][timeout:5];out count;"

# Tight wall-clock cap. The probe runs before the heavy enrichment query, so a
# delay here dwarfs the Overpass-timeout budget upstream and only delays the
# fail-fast verdict the workflow is waiting for.
_DEFAULT_TIMEOUT_S = 8.0


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    # Sentinel: route through SafeFormatter so any raw exception text
    # logged via %s in this script is sanitised at the formatter layer.
    setup_script_logging(level)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--timeout",
        type=float,
        default=_DEFAULT_TIMEOUT_S,
        help=f"Per-request wall-clock cap in seconds (default: {_DEFAULT_TIMEOUT_S}).",
    )
    parser.add_argument(
        "--endpoint",
        default=None,
        help=("Override the trusted Overpass endpoint URL. Must already be on " f"the allow-list: {DEFAULT_OVERPASS_ENDPOINTS!r}."),
    )
    parser.add_argument(
        "--allow-skip",
        action="store_true",
        help=(
            "Downgrade network-failure exits (2) to 0 so the calling workflow "
            "can proceed without OSM enrichment instead of failing the job."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Emit DEBUG-level logs.",
    )
    return parser.parse_args(argv)


def _resolve_endpoint(override: str | None) -> str:
    if override is None:
        return get_overpass_endpoint()
    if override in DEFAULT_OVERPASS_ENDPOINTS:
        return override
    LOGGER.warning(
        "Refusing override %s; not on the trusted Overpass allow-list — using default.",
        sanitize_log_arg(override),
    )
    return DEFAULT_OVERPASS_ENDPOINTS[0]


def _send_probe(session: requests.Session, endpoint: str, timeout_s: float) -> requests.Response | None:
    try:
        return request_safe(
            session,
            endpoint,
            method="POST",
            max_bytes=64 * 1024,
            timeout=timeout_s,
            allowed_content_types=("application/json", "application/osm3s+xml"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": _USER_AGENT,
            },
            data={"data": _PROBE_QUERY},
        )
    except requests.RequestException as exc:
        LOGGER.error(
            "Overpass probe failed (network): %s",
            sanitize_log_arg(type(exc).__name__),
        )
        return None
    except ValueError as exc:
        LOGGER.error(
            "Overpass probe rejected by request_safe: %s",
            sanitize_log_arg(type(exc).__name__),
        )
        return None


def _probe(endpoint: str, timeout_s: float) -> int:
    session = session_with_retries(
        user_agent=_USER_AGENT,
        timeout=(min(3.0, timeout_s), timeout_s),
        allowed_methods=("GET", "POST"),
    )
    try:
        response = _send_probe(session, endpoint, timeout_s)
        if response is None:
            return 2
        return _evaluate_response(endpoint, response)
    finally:
        session.close()


def _evaluate_response(endpoint: str, response: requests.Response) -> int:
    if response.status_code != 200:
        LOGGER.error("Overpass probe returned HTTP %s", response.status_code)
        return 1

    try:
        payload = response.json()
    except (ValueError, RecursionError):
        # ``RecursionError`` defends against a JSON depth-bomb planted in
        # a degraded Overpass response (see ``tests/test_sentinel_json_audit_walker.py``)
        # — the audit walker enforces that every ``response.json()`` call
        # carries a RecursionError-tolerant guard.
        LOGGER.error("Overpass probe returned non-JSON / depth-bomb payload")
        return 1

    if not isinstance(payload, dict) or "elements" not in payload:
        LOGGER.error("Overpass probe payload missing expected 'elements' key")
        return 1

    elements = payload.get("elements") or []
    LOGGER.info(
        "Overpass smoke check OK (endpoint=%s, elements=%d)",
        sanitize_log_arg(endpoint),
        len(elements) if isinstance(elements, list) else 0,
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    _configure_logging(args.verbose)

    endpoint = _resolve_endpoint(args.endpoint)
    code = _probe(endpoint, max(1.0, float(args.timeout)))

    if code == 2 and args.allow_skip:
        LOGGER.warning("Overpass appears unreachable; --allow-skip is set so the " "workflow may proceed without OSM enrichment.")
        return 0
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
