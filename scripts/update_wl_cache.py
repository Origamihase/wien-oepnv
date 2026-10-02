#!/usr/bin/env python3
"""Fetch and cache Wiener Linien events."""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.feed.logging_safe import setup_script_logging  # noqa: E402
from src.providers.wiener_linien import fetch_events  # noqa: E402  (import after path setup)
from src.providers.wl_plausibility import collected_corrections, record_corrections  # noqa: E402
from src.utils.cache import DataDegradationError, write_cache  # noqa: E402
from src.utils.serialize import serialize_for_cache  # noqa: E402


logger = logging.getLogger("update_wl_cache")

# Contradictions between the sources of a WL item and how they were settled
# (see ``src/providers/wl_plausibility.py``). Committed with the cache.
PLAUSIBILITY_ANOMALIES = REPO_ROOT / "data" / "wl_plausibility_anomalies.json"


def record_plausibility_anomalies(path: Path = PLAUSIBILITY_ANOMALIES) -> None:
    """Keep this fetch's plausibility corrections in *path*; never fails the run."""
    try:
        today = datetime.now(ZoneInfo("Europe/Vienna")).date()
        new = record_corrections(path, collected_corrections(), today)
    except OSError as exc:
        logger.warning("WL-Plausibilitätssammlung nicht geschrieben (%s).", type(exc).__name__)
        return
    if new:
        logger.info("WL-Plausibilität: %d neue Auffälligkeit(en) gesammelt.", new)


def configure_logging() -> None:
    """Configure root logging with the project's SafeFormatter."""

    # Sentinel: route through SafeFormatter so any raw exception text
    # logged via %s in this script is sanitised at the formatter layer.
    setup_script_logging(logging.INFO)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def main() -> int:
    """Entry point for refreshing the Wiener Linien cache."""

    configure_logging()
    try:
        items = fetch_events()
    except Exception:  # pragma: no cover - defensive
        logger.exception(
            "Failed to fetch Wiener Linien events; keeping existing cache.",
        )
        return 1

    record_plausibility_anomalies()

    # Defensive: fetch_events() is annotated list[...], so mypy --strict
    # sees this runtime contract guard as unreachable. Keep it regardless —
    # a provider regression returning a non-list must not corrupt the cache.
    if not isinstance(items, list):
        logger.error(  # type: ignore[unreachable]
            "Unexpected fetch_events() return type %s; keeping existing cache.",
            type(items).__name__,
        )
        return 1

    if not items:
        logger.warning(
            "Fetched 0 events; keeping existing cache."
        )
        return 1

    serialized_items = [serialize_for_cache(item) for item in items]
    try:
        write_cache("wl", serialized_items)
    except DataDegradationError:
        # ``write_cache`` refuses not only an empty payload but also a
        # drastically smaller one (< 20 % of the existing cache) to avoid
        # overwriting a healthy cache during a partial upstream outage.
        # Keep the existing cache and exit non-zero instead of crashing
        # the cron step (mirrors scripts/update_baustellen_cache.py).
        logger.warning(
            "Degraded WL payload (%d events); keeping existing cache.",
            len(serialized_items),
        )
        return 1
    logger.info("Updated Wiener Linien cache with %d events.", len(serialized_items))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
