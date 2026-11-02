#!/usr/bin/env python3
"""Collect the rail lines that serve each ÖBB station, via HAFAS.

Stage 2 of the line plausibility check (audit 2026-09-25, A.14). Stage 1
gave every Wiener Linien station its ``wl_lines``; the WL data has no
routes for S-Bahn lines, so the ÖBB stations of Vienna and the commuter
belt get theirs from HAFAS departure boards.

What this records is what *currently runs*: HAFAS answers with the
timetable in force, construction included. Two measures keep a closure
from erasing a line:

* two sample dates, the next Tuesday and the Tuesday five weeks later;
* every line keeps the date it was last seen and is dropped only after
  :data:`RETENTION_DAYS` (three years) without a sighting.

A line is only remembered once it has been seen: a closure that began
before this script first saw the line still hides it (the S80 at
Hütteldorf, closed since 2026-09-07). The stage-3 check must therefore
never read "HAFAS has no S80 at Hütteldorf" as proof that the S80 does not
stop there.

Per station and run: one ``LocMatch`` the first time, and again on every
run while the station has no line (the HAFAS station id is kept; see
:func:`pick_rail_location` for which hit counts and :func:`short_name` for
the second query), then two windows (06:00–09:00 and 15:00–18:00) on each
date, rail classes only, in the request form ``public-transport/hafas-client``
uses for ÖBB (probe runs of 2026-09-25). If those boards show no line, the
other stops at the same place get the same four requests
(:func:`same_place_stops`). Requests are paced; five
consecutive failures stop the run and keep what was collected. Writes
``data/oebb_station_lines.json`` only.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import date
from logging import DEBUG, INFO
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.feed.config import validate_path
from src.feed.logging_safe import setup_script_logging
from src.oebb_lines.hafas import (
    _HAFAS_COORD_SCALE,
    _HAUPTBAHNHOF_RE,
    _LINE_ID_RE,
    _LINE_TOKEN_RE,
    BOARD_MAX_BYTES,
    LOC_MATCH_CANDIDATES,
    LOCAL_RAIL_CLASSES,
    LOGGER,
    MAX_CONSECUTIVE_FAILURES,
    MAX_JOURNEYS,
    MAX_LOGGED_PRODUCTS,
    MAX_MATCH_DISTANCE_M,
    MAX_SAME_PLACE_STOPS,
    MAX_STATE_BYTES,
    PAUSE_SECONDS,
    RAIL_CLASSES,
    RETENTION_DAYS,
    SAME_PLACE_M,
    SAMPLE_WINDOWS,
    SECOND_DATE_OFFSET_DAYS,
    SHORT_FORM,
    STATE_VERSION,
    RailLocation,
    Station,
    _answer,
    _clean,
    _coordinate,
    _coordinates,
    _rail_candidate,
    board_request,
    board_summary,
    describe_candidates,
    line_token,
    lines_from_board,
    merge_lines,
    pick_rail_location,
    rail_locations,
    same_place_stops,
    sample_dates,
    select_stations,
    short_name,
)
from src.oebb_lines.refresh import (
    Post,
    RefreshResult,
    _Boards,
    _call,
    _describe,
    _FailureRun,
    _is_resolved,
    _locate,
    _log_no_line,
    _resolve,
    _sample_boards,
    _store,
    _try_same_place,
    load_state,
    refresh,
    write_state,
)
from src.utils.files import read_capped_json

__all__ = [
    "BOARD_MAX_BYTES",
    "DEFAULT_OUTPUT",
    "DEFAULT_STATIONS",
    "LOCAL_RAIL_CLASSES",
    "LOC_MATCH_CANDIDATES",
    "LOGGER",
    "MAX_CONSECUTIVE_FAILURES",
    "MAX_JOURNEYS",
    "MAX_LOGGED_PRODUCTS",
    "MAX_MATCH_DISTANCE_M",
    "MAX_SAME_PLACE_STOPS",
    "MAX_STATE_BYTES",
    "PAUSE_SECONDS",
    "Post",
    "RAIL_CLASSES",
    "REPO_ROOT",
    "RETENTION_DAYS",
    "RailLocation",
    "RefreshResult",
    "SAME_PLACE_M",
    "SAMPLE_WINDOWS",
    "SECOND_DATE_OFFSET_DAYS",
    "SHORT_FORM",
    "STATE_VERSION",
    "Station",
    "_Boards",
    "_FailureRun",
    "_HAFAS_COORD_SCALE",
    "_HAUPTBAHNHOF_RE",
    "_LINE_ID_RE",
    "_LINE_TOKEN_RE",
    "_answer",
    "_call",
    "_clean",
    "_coordinate",
    "_coordinates",
    "_describe",
    "_is_resolved",
    "_locate",
    "_log_no_line",
    "_rail_candidate",
    "_resolve",
    "_sample_boards",
    "_store",
    "_try_same_place",
    "board_request",
    "board_summary",
    "describe_candidates",
    "line_token",
    "lines_from_board",
    "load_state",
    "main",
    "merge_lines",
    "pick_rail_location",
    "rail_locations",
    "refresh",
    "same_place_stops",
    "sample_dates",
    "select_stations",
    "short_name",
    "write_state",
]

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATIONS = REPO_ROOT / "data" / "stations.json"
DEFAULT_OUTPUT = REPO_ROOT / "data" / "oebb_station_lines.json"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--stations", type=Path, default=DEFAULT_STATIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--pause", type=float, default=PAUSE_SECONDS, help="seconds between requests")
    parser.add_argument("--limit", type=int, default=0, help="only the first N stations (0 = all)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    setup_script_logging(DEBUG if args.verbose else INFO)

    output = validate_path(args.output, "--output")
    directory = read_capped_json(args.stations, label="Stations", logger=LOGGER)
    entries = directory.get("stations") if isinstance(directory, dict) else None
    if not isinstance(entries, list):
        LOGGER.error("Station directory missing or invalid")
        return 1
    stations = select_stations(entries)
    if args.limit > 0:
        stations = stations[: args.limit]

    today = date.today()
    state = load_state(output)
    result = refresh(stations, state, today, pause=max(args.pause, 0.0))
    if result.checked == 0 and not state:
        LOGGER.error("No station answered; %s left unchanged", _clean(output.name))
        return 1
    write_state(output, state, today)
    LOGGER.info(
        "ÖBB station lines: %d checked, %d failed, %d without HAFAS match, %d stations in file%s",
        result.checked,
        result.failed,
        result.unresolved,
        len(state),
        " (run stopped early)" if result.aborted else "",
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
