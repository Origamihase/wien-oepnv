#!/usr/bin/env python3
"""Fetch and cache VOR events"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from requests.exceptions import RequestException

try:  # pragma: no cover - support editable installs
    from utils.logging_setup import ensure_rotating_file_logging
except ModuleNotFoundError:  # pragma: no cover
    from src.utils.logging_setup import ensure_rotating_file_logging  # type: ignore


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def _seed_station_ids_from_file() -> None:
    """Populate ``VOR_STATION_IDS`` from repository defaults if unset."""

    if os.getenv("VOR_STATION_IDS"):
        return

    try:
        from utils.stations import vor_station_ids
    except ModuleNotFoundError:  # pragma: no cover - fallback for src layout
        from src.utils.stations import vor_station_ids  # type: ignore

    ids_from_directory = ",".join(vor_station_ids())
    if ids_from_directory:
        os.environ["VOR_STATION_IDS"] = ids_from_directory
        return

    station_file = REPO_ROOT / "data" / "vor_station_ids_wien.txt"
    try:
        raw = station_file.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return

    parts = [segment.strip() for segment in raw.replace("\n", ",").split(",")]
    station_ids = ",".join(part for part in parts if part)
    if station_ids:
        os.environ["VOR_STATION_IDS"] = station_ids


_seed_station_ids_from_file()

from providers.vor import (  # noqa: E402  (import after path setup)
    MAX_REQUESTS_PER_DAY,
    fetch_events,
    load_request_count,
)
from utils.cache import write_cache  # noqa: E402
from utils.serialize import serialize_for_cache  # noqa: E402


logger = logging.getLogger("update_vor_cache")


def configure_logging() -> None:
    """Configure root logging for the update run."""

    ensure_rotating_file_logging()

    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(
            handler, logging.FileHandler
        ):
            handler.setLevel(logging.INFO)
            handler.setFormatter(logging.Formatter("%(message)s"))
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
        return 0
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

    serialized_items = [serialize_for_cache(item) for item in items]
    write_cache("vor", serialized_items)
    logger.info("VOR: Cache mit %d Einträgen aktualisiert.", len(serialized_items))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
