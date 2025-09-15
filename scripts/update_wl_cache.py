#!/usr/bin/env python3
"""Fetch and cache Wiener Linien events."""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from providers.wiener_linien import fetch_events  # noqa: E402  (import after path setup)
from utils.cache import write_cache  # noqa: E402


logger = logging.getLogger("update_wl_cache")


def _serialize(value: Any) -> Any:
    """Recursively convert unsupported types into JSON serializable values."""

    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _serialize(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    if isinstance(value, set):
        serialized = [_serialize(item) for item in value]
        return sorted(serialized, key=str)
    return value


def configure_logging() -> None:
    """Configure root logging for the update run."""

    logging.basicConfig(level=logging.INFO, format="%(message)s")
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

    if not isinstance(items, list):
        logger.error(
            "Unexpected fetch_events() return type %s; keeping existing cache.",
            type(items).__name__,
        )
        return 1

    serialized_items = [_serialize(item) for item in items]
    write_cache("wl", serialized_items)
    logger.info("Updated Wiener Linien cache with %d events.", len(serialized_items))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
