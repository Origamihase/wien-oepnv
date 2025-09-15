#!/usr/bin/env python3
"""Fetch and cache VOR events."""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from requests.exceptions import RequestException


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from providers.vor import (  # noqa: E402  (import after path setup)
    MAX_REQUESTS_PER_DAY,
    fetch_events,
    load_request_count,
    save_request_count,
)
from utils.cache import write_cache  # noqa: E402


logger = logging.getLogger("update_vor_cache")


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


def _now_local() -> datetime:
    return datetime.now().astimezone(ZoneInfo("Europe/Vienna"))


def _todays_request_count(now_local: datetime) -> int:
    stored_date, stored_count = load_request_count()
    return stored_count if stored_date == now_local.date().isoformat() else 0


def _limit_reached(now_local: datetime) -> bool:
    todays_count = _todays_request_count(now_local)
    if todays_count >= MAX_REQUESTS_PER_DAY:
        logger.info(
            "VOR: Tageslimit von %s Anfragen erreicht (%s) – überspringe Cache-Aktualisierung.",
            MAX_REQUESTS_PER_DAY,
            todays_count,
        )
        return True
    return False


def main() -> int:
    """Entry point for refreshing the VOR cache."""

    configure_logging()

    now_local = _now_local()
    if _limit_reached(now_local):
        return 0

    try:
        items = fetch_events()
    except RequestException:
        logger.warning(
            "VOR: API nicht erreichbar – behalte bestehenden Cache bei.",
            exc_info=True,
        )
        return 1
    except Exception:  # pragma: no cover - defensive
        logger.exception(
            "VOR: Fehler beim Abrufen der Daten – behalte bestehenden Cache bei.",
        )
        return 1

    if not isinstance(items, list):
        logger.error(
            "VOR: Unerwarteter Rückgabetyp %s – behalte bestehenden Cache bei.",
            type(items).__name__,
        )
        return 1

    now_local = _now_local()
    if _limit_reached(now_local):
        return 0

    serialized_items = [_serialize(item) for item in items]
    write_cache("vor", serialized_items)
    save_request_count(now_local)
    logger.info("VOR: Cache mit %d Einträgen aktualisiert.", len(serialized_items))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
