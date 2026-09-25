#!/usr/bin/env python3
"""Collect the rail lines that serve each ÖBB station, via HAFAS.

Stage 2 of the line plausibility check (audit 2026-09-25, A.14). Stage 1
gave every Wiener Linien station its ``wl_lines``; the WL data has no
routes for S-Bahn lines, so the ÖBB stations of Vienna and the commuter
belt get theirs from HAFAS departure boards.

What this records is what *currently runs*: HAFAS answers with the
timetable in force, construction included. Two measures keep a short
closure from erasing a line:

* two sample dates, the next Tuesday and the Tuesday five weeks later;
* every line keeps the date it was last seen and is dropped only after
  :data:`RETENTION_DAYS` without a sighting.

A closure lasting longer than that — the operator notes that some run for
years — still hides its line. The stage-3 check must therefore never read
"HAFAS has no S80 at Hütteldorf" as proof that the S80 does not stop there.

Per station and run: one ``LocMatch`` the first time (the HAFAS station id
is kept and checked against the station's coordinates), then two windows
(06:00–09:00 and 15:00–18:00) on each date, rail classes only, in the
request form ``public-transport/hafas-client`` uses for ÖBB (probe runs of
2026-09-25). Requests are paced; five consecutive failures stop the run
and keep what was collected. Writes ``data/oebb_station_lines.json`` only.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import requests

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.feed.config import validate_path
from src.feed.logging_safe import setup_script_logging
from src.places.hafas_client import (
    HafasLocation,
    HafasProfileError,
    enrich_station_with_hafas,
    post_mgate,
)
from src.utils.files import atomic_write, read_capped_json
from src.utils.geo import calculate_distance_meters
from src.utils.logging import sanitize_log_arg
from src.utils.serialize import scrub_trojan_source_primitives

LOGGER = logging.getLogger("oebb_station_lines")

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATIONS = REPO_ROOT / "data" / "stations.json"
DEFAULT_OUTPUT = REPO_ROOT / "data" / "oebb_station_lines.json"
STATE_VERSION = 1

RETENTION_DAYS = 56
SECOND_DATE_OFFSET_DAYS = 35
# (start time, duration in minutes)
SAMPLE_WINDOWS: tuple[tuple[str, int], ...] = (("060000", 180), ("150000", 180))
# Without ``maxJny`` HAFAS returned only ~50 departures (probe, 2026-09-25).
MAX_JOURNEYS = 400
# ÖBB rail product classes (hafas-client p/oebb/products.js): ICE/RJ, IC/EC,
# D/EN, R/REX, S-Bahn.
RAIL_CLASSES = 1 | 2 | 4 | 8 | 16 | 32 | 4096
BOARD_MAX_BYTES = 5 * 1024 * 1024
PAUSE_SECONDS = 0.5
MAX_CONSECUTIVE_FAILURES = 5
# A LocMatch hit further away than this is a different place of that name.
MAX_MATCH_DISTANCE_M = 2000.0
MAX_STATE_BYTES = 5 * 1024 * 1024

# ``at:obb:vor|S45:`` → ``S45``
_LINE_ID_RE = re.compile(r"\|([A-Z]{1,4}\d{1,3}):?$")
_LINE_TOKEN_RE = re.compile(r"^[A-Z]{1,4}\d{1,3}$")


@dataclass(frozen=True)
class Station:
    """An ÖBB station in scope: Vienna or the commuter belt."""

    bst_id: str
    name: str
    latitude: float | None
    longitude: float | None


def _clean(value: object) -> str:
    return str(sanitize_log_arg(str(value)))


def _coordinate(value: object) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def select_stations(entries: Sequence[object]) -> list[Station]:
    """ÖBB stations (with ``bst_id``) in Vienna or the commuter belt."""
    selected: list[Station] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        bst_id = entry.get("bst_id")
        name = entry.get("name")
        if not isinstance(bst_id, str) or not bst_id.isdigit():
            continue
        if not isinstance(name, str) or not name.strip():
            continue
        if str(entry.get("type", "")).startswith("manual"):
            continue
        if not (entry.get("in_vienna") is True or entry.get("pendler") is True):
            continue
        selected.append(
            Station(
                bst_id=bst_id,
                name=name.strip(),
                latitude=_coordinate(entry.get("latitude")),
                longitude=_coordinate(entry.get("longitude")),
            )
        )
    return selected


def sample_dates(today: date) -> tuple[date, date]:
    """The next Tuesday after *today*, and the Tuesday five weeks later."""
    first = today + timedelta(days=(1 - today.weekday()) % 7 or 7)
    return first, first + timedelta(days=SECOND_DATE_OFFSET_DAYS)


def board_request(ext_id: str, day: date, start: str, minutes: int) -> dict[str, object]:
    """Rail departures of one window, in the form hafas-client sends for ÖBB."""
    return {
        "meth": "StationBoard",
        "req": {
            "type": "DEP",
            "date": day.strftime("%Y%m%d"),
            "time": start,
            "stbLoc": {"type": "S", "lid": f"A=1@L={ext_id}@"},
            "jnyFltrL": [{"type": "PROD", "mode": "INC", "value": str(RAIL_CLASSES)}],
            "dur": minutes,
            "maxJny": MAX_JOURNEYS,
        },
    }


def line_token(product: object) -> str | None:
    """``S 45`` (``at:obb:vor|S45:``) → ``S45``; ``None`` for anything else.

    Long-distance trains (RJ, IC, WESTbahn) carry train numbers, not lines,
    and rail replacement buses (``BusSV910``, "Schienenersatzverkehr") are
    no line of the station.
    """
    if not isinstance(product, dict):
        return None
    context = product.get("prodCtx")
    if not isinstance(context, dict):
        return None
    category = str(context.get("catOut", "")).strip()
    if category.casefold() == "bus" or "ersatzverkehr" in str(context.get("catOutL", "")).casefold():
        return None
    line_id = context.get("lineId")
    if isinstance(line_id, str):
        match = _LINE_ID_RE.search(line_id.strip())
        if match:
            return match.group(1)
    line = context.get("line")
    if not isinstance(line, str) or not line.strip():
        return None
    token = re.sub(r"\s+", "", f"{category}{line}").upper()
    return token if _LINE_TOKEN_RE.match(token) else None


def lines_from_board(payload: object) -> set[str] | None:
    """The lines on a ``StationBoard`` answer, or ``None`` if it failed."""
    if not isinstance(payload, dict):
        return None
    services = payload.get("svcResL")
    if not isinstance(services, list) or not services or not isinstance(services[0], dict):
        return None
    service = services[0]
    if service.get("err") != "OK":
        return None
    res = service.get("res")
    if not isinstance(res, dict):
        return set()
    journeys = res.get("jnyL")
    if isinstance(journeys, list) and len(journeys) >= MAX_JOURNEYS:
        LOGGER.warning("StationBoard hit maxJny=%d; later departures are missing", MAX_JOURNEYS)
    common = res.get("common")
    products = common.get("prodL") if isinstance(common, dict) else None
    if not isinstance(products, list):
        return set()
    return {token for token in map(line_token, products) if token}


def merge_lines(
    known: Mapping[str, str], seen: set[str], today: date
) -> dict[str, str]:
    """Stamp *seen* lines with *today*; drop those unseen for too long."""
    cutoff = today - timedelta(days=RETENTION_DAYS)
    merged: dict[str, str] = {}
    for line, last_seen in known.items():
        try:
            if date.fromisoformat(last_seen) >= cutoff:
                merged[line] = last_seen
        except (TypeError, ValueError):
            continue
    for line in seen:
        merged[line] = today.isoformat()
    return dict(sorted(merged.items()))


def _matches_station(station: Station, location: HafasLocation) -> bool:
    if station.latitude is None or station.longitude is None:
        return False
    distance = calculate_distance_meters(
        station.latitude, station.longitude, location["lat"], location["lon"]
    )
    return distance <= MAX_MATCH_DISTANCE_M


Post = Callable[..., object]
Locate = Callable[[str], HafasLocation | None]


@dataclass
class RefreshResult:
    checked: int = 0
    failed: int = 0
    unresolved: int = 0
    aborted: bool = False


def refresh(
    stations: Sequence[Station],
    state: dict[str, Any],
    today: date,
    *,
    post: Post = post_mgate,
    locate: Locate = enrich_station_with_hafas,
    pause: float = PAUSE_SECONDS,
) -> RefreshResult:
    """Update *state* (``{bst_id: entry}``) in place for *stations*."""
    result = RefreshResult()
    failures_in_a_row = 0
    dates = sample_dates(today)
    in_scope = {station.bst_id for station in stations}
    for stale in [key for key in state if key not in in_scope]:
        del state[stale]

    for station in stations:
        entry = state.setdefault(station.bst_id, {"lines": {}})
        entry["name"] = station.name
        ext_id = entry.get("hafas_ext_id")
        if not isinstance(ext_id, str) or not ext_id:
            location = locate(station.name)
            time.sleep(pause)
            if location is None or not _matches_station(station, location):
                result.unresolved += 1
                LOGGER.info("No HAFAS match near %s", _clean(station.name))
                continue
            ext_id = location["extId"]
            entry["hafas_ext_id"] = ext_id

        seen: set[str] = set()
        answered = False
        for day in dates:
            for start, minutes in SAMPLE_WINDOWS:
                try:
                    payload = post(
                        [board_request(ext_id, day, start, minutes)],
                        max_bytes=BOARD_MAX_BYTES,
                    )
                except (HafasProfileError, requests.RequestException, ValueError) as exc:
                    payload = None
                    LOGGER.warning(
                        "StationBoard failed for %s: %s",
                        _clean(station.name),
                        _clean(type(exc).__name__),
                    )
                time.sleep(pause)
                lines = lines_from_board(payload)
                if lines is None:
                    failures_in_a_row += 1
                    if failures_in_a_row >= MAX_CONSECUTIVE_FAILURES:
                        LOGGER.error(
                            "%d HAFAS failures in a row; stopping, keeping what was collected",
                            failures_in_a_row,
                        )
                        result.aborted = True
                        return result
                    continue
                failures_in_a_row = 0
                answered = True
                seen |= lines

        if not answered:
            result.failed += 1
            continue
        known = entry.get("lines")
        entry["lines"] = merge_lines(known if isinstance(known, dict) else {}, seen, today)
        entry["checked"] = today.isoformat()
        result.checked += 1
    return result


def load_state(path: Path) -> dict[str, Any]:
    """The station entries of *path*, or ``{}`` for a missing/invalid file."""
    payload = read_capped_json(path, MAX_STATE_BYTES, label="ÖBB station lines", logger=LOGGER)
    if not isinstance(payload, dict):
        return {}
    stations = payload.get("stations")
    if not isinstance(stations, dict):
        return {}
    return {
        str(key): value
        for key, value in stations.items()
        if isinstance(value, dict)
    }


def write_state(path: Path, stations: Mapping[str, Any], today: date) -> None:
    """Write the state atomically; keys sorted for stable diffs."""
    document = {
        "version": STATE_VERSION,
        "updated": today.isoformat(),
        "retention_days": RETENTION_DAYS,
        "source": "HAFAS (ÖBB Scotty) StationBoard, rail classes; see scripts/update_oebb_station_lines.py",
        "stations": dict(sorted(stations.items(), key=lambda item: item[0].zfill(12))),
    }
    scrubbed = scrub_trojan_source_primitives(document)
    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(path, mode="w", encoding="utf-8", permissions=0o644) as handle:
        json.dump(scrubbed, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--stations", type=Path, default=DEFAULT_STATIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--pause", type=float, default=PAUSE_SECONDS, help="seconds between requests")
    parser.add_argument("--limit", type=int, default=0, help="only the first N stations (0 = all)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    setup_script_logging(logging.DEBUG if args.verbose else logging.INFO)

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
