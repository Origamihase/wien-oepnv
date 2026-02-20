#!/usr/bin/env python3
"""Fetch and cache ÖBB events."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from requests.exceptions import RequestException


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from providers.oebb import fetch_events  # noqa: E402  (import after path setup)
from utils.cache import write_cache  # noqa: E402
from utils.serialize import serialize_for_cache  # noqa: E402


logger = logging.getLogger("update_oebb_cache")


def configure_logging() -> None:
    """Configure root logging for the update run."""

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def main() -> int:
    """Entry point for refreshing the ÖBB cache."""

    configure_logging()
    try:
        items = fetch_events()
    except RequestException:
        logger.warning(
            "Network error while fetching ÖBB events; keeping existing cache.",
            exc_info=True,
        )
        return 1
    except Exception:  # pragma: no cover - defensive
        logger.exception(
            "Failed to fetch ÖBB events; keeping existing cache.",
        )
        return 1

    if not isinstance(items, list):
        logger.error(
            "Unexpected fetch_events() return type %s; keeping existing cache.",
            type(items).__name__,
        )
        return 1

    if not items:
        logger.error(
            "Fetched 0 events (unexpected empty list); keeping existing cache."
        )
        return 1

    serialized_items = [serialize_for_cache(item) for item in items]
    write_cache("oebb", serialized_items)
    logger.info("Updated ÖBB cache with %d events.", len(serialized_items))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
