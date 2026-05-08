#!/usr/bin/env python3
"""Fetch and cache VOR events"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, UTC
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from requests.exceptions import RequestException


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.utils.files import read_capped_text  # noqa: E402

# Security: per-loader byte cap. Pre-fix ``_seed_station_ids_from_file``
# read ``data/vor_station_ids_wien.txt`` via ``read_text(encoding=
# "utf-8")`` with NO size cap — a planted huge file at the seed path
# raised ``MemoryError`` past the ``except (FileNotFoundError, OSError)``
# catch and crashed the daily VOR cache update at startup before any
# network request runs. The seed file is a small CSV/newline list of
# station IDs, typically <1 KiB; 5 MiB is >>1000x legit while still
# rejecting GiB-sized planted attacks.
MAX_VOR_STATION_IDS_FILE_BYTES = 5 * 1024 * 1024


def _seed_station_ids_from_file() -> None:
    """Populate ``VOR_STATION_IDS`` from repository defaults if unset."""

    if os.getenv("VOR_STATION_IDS"):
        return

    try:
        from src.utils.stations import vor_station_ids
    except ModuleNotFoundError:  # pragma: no cover - fallback for src layout
        from utils.stations import vor_station_ids  # type: ignore[no-redef]

    ids_from_directory = ",".join(vor_station_ids())
    if ids_from_directory:
        os.environ["VOR_STATION_IDS"] = ids_from_directory
        return

    station_file = REPO_ROOT / "data" / "vor_station_ids_wien.txt"
    raw = read_capped_text(
        station_file,
        MAX_VOR_STATION_IDS_FILE_BYTES,
        label="VOR station IDs seed",
    )
    if raw is None:
        return

    parts = [segment.strip() for segment in raw.replace("\n", ",").split(",")]
    station_ids = ",".join(part for part in parts if part)
    if station_ids:
        os.environ["VOR_STATION_IDS"] = station_ids


_seed_station_ids_from_file()

from src.providers.vor import (  # noqa: E402  (import after path setup)
    MAX_REQUESTS_PER_DAY,
    fetch_events,
    load_request_count,
    get_configured_stations,
    select_stations_for_run,
)
from src.feed.logging_safe import setup_script_logging  # noqa: E402
from src.utils.cache import write_cache, write_status  # noqa: E402
from src.utils.serialize import serialize_for_cache  # noqa: E402


__all__ = ["MAX_REQUESTS_PER_DAY"]


logger = logging.getLogger("update_vor_cache")


def configure_logging() -> None:
    """Configure root logging with the project's SafeFormatter."""

    # Sentinel: route through SafeFormatter so any raw exception text
    # logged via %s in this script is sanitised at the formatter layer.
    setup_script_logging(logging.INFO)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _now_local() -> datetime:
    return datetime.now(UTC).astimezone(ZoneInfo("Europe/Vienna"))


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


def _record_status(
    *,
    status: str,
    stations_queried: int,
    events_collected: int | None,
    now_local: datetime,
) -> None:
    """Persist a heartbeat for the VOR cache run.

    The marker is committed even when ``events.json`` would otherwise stay
    byte-identical (e.g. a stretch of empty provider responses), so the most
    recent successful run stays visible in git history.
    """

    todays_count = _todays_request_count(now_local)
    payload: dict[str, Any] = {
        "last_run_at": now_local.astimezone(UTC).isoformat(),
        "last_run_at_local": now_local.isoformat(),
        "status": status,
        "stations_queried": stations_queried,
        "requests_used_today": todays_count,
        "daily_limit": MAX_REQUESTS_PER_DAY,
    }
    if events_collected is not None:
        payload["events_collected"] = events_collected
    try:
        write_status("vor", payload)
    except Exception:  # pragma: no cover - defensive
        logger.exception("VOR: Status-Marker konnte nicht geschrieben werden.")


def main() -> int:
    """Entry point for refreshing the VOR cache."""

    # --- SAFETY CHECK ---
    # VOR erlaubt strikt max. 100 Requests pro Tag.
    # Wir berechnen den maximalen Verbrauch basierend auf der Konfiguration.
    stations = get_configured_stations()
    stations_for_run = select_stations_for_run(stations)
    # The limit is based on total stations fetched over a day.
    # select_stations_for_run limits the stations PER RUN.
    # So daily usage is (stations_per_run) * (runs_per_day).

    # However, to be extra safe as per instruction, we use the logic:
    # "PROJECTED_DAILY_USAGE = len(stations_for_run) * 24"

    DAILY_RUNS_ASSUMED = 24  # Wir erzwingen stündliche Ausführung
    # Note: select_stations_for_run returns the subset that WILL be used.
    # If the user has 10 stations but we only rotate 2 per run, the cost is 2 * 24 = 48 reqs/day.
    STATIONS_COUNT = len(stations_for_run)
    PROJECTED_USAGE = STATIONS_COUNT * DAILY_RUNS_ASSUMED

    print(f"🔒 VOR Safety Check: {STATIONS_COUNT} Stationen/Run * {DAILY_RUNS_ASSUMED} Runs = {PROJECTED_USAGE} Requests/Tag")

    if PROJECTED_USAGE > 90: # Puffer von 10 Requests für Tests lassen
        print(f"❌ CRITICAL: Konfiguration würde {PROJECTED_USAGE} Requests erzeugen (Limit: 100).")
        print("ABBRUCH! Bitte Stationen reduzieren oder Intervall prüfen.")
        # Ensure we exit if safety check fails
        return 1

    configure_logging()

    now_local = _now_local()
    if _limit_reached(now_local):
        _record_status(
            status="skipped_quota",
            stations_queried=0,
            events_collected=None,
            now_local=now_local,
        )
        return 0

    try:
        # Pass the pre-selected/calculated stations to avoid re-resolution or discrepancy
        items = fetch_events(station_ids=stations_for_run)
    except RequestException as exc:
        # Security: ``VorAuth`` (src/providers/vor.py:701) injects the
        # VAO ``accessId`` query parameter into every prepared request
        # whose URL starts with ``VOR_BASE_URL``. When the network layer
        # fails, the resulting ``RequestException`` (or its
        # ``__context__`` chain) embeds the post-VorAuth URL — including
        # ``accessId=<SECRET>`` — in its message. ``exc_info=True`` would
        # write that traceback (and any chained ``__context__``
        # exceptions) verbatim to errors.log and CI-runner stdout
        # (clear-text-logging dataflow, mirrors the 2026-05-08 fix in
        # src/utils/http.py:_resolve_hostname_safe). Logging only the
        # exception class suppresses the URL while preserving the
        # failure-mode diagnostic.
        logger.warning(
            "VOR: API nicht erreichbar (%s) – behalte bestehenden Cache bei.",
            type(exc).__name__,
        )
        _record_status(
            status="api_unreachable",
            stations_queried=STATIONS_COUNT,
            events_collected=None,
            now_local=now_local,
        )
        return 0
    except Exception as exc:  # pragma: no cover - defensive
        # Same clear-text-logging concern as the RequestException branch
        # above: an unexpected exception escaping ``fetch_events`` may
        # carry the post-VorAuth URL (e.g. via a re-raised
        # ``__context__``). ``logger.exception`` is shorthand for
        # ``logger.error(..., exc_info=True)``, so it has the same leak
        # surface. Drop ``exc_info`` and log the class name only.
        logger.error(
            "VOR: Fehler beim Abrufen der Daten (%s) – behalte bestehenden Cache bei.",
            type(exc).__name__,
        )
        _record_status(
            status="error",
            stations_queried=STATIONS_COUNT,
            events_collected=None,
            now_local=now_local,
        )
        return 1

    serialized_items = [serialize_for_cache(item) for item in items]
    write_cache("vor", serialized_items)
    logger.info("VOR: Cache mit %d Einträgen aktualisiert.", len(serialized_items))
    _record_status(
        status="ok",
        stations_queried=STATIONS_COUNT,
        events_collected=len(serialized_items),
        now_local=now_local,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
