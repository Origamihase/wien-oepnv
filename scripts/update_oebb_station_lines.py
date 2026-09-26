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
import json
import re
import sys
import time
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import product
from logging import DEBUG, INFO, getLogger
from pathlib import Path
from typing import Any

import requests

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.feed.config import validate_path
from src.feed.logging_safe import setup_script_logging
from src.places.hafas_client import HafasProfileError, loc_match_request, post_mgate
from src.utils.files import atomic_write, read_capped_json
from src.utils.geo import calculate_distance_meters
from src.utils.logging import sanitize_log_arg
from src.utils.serialize import scrub_trojan_source_primitives

LOGGER = getLogger("oebb_station_lines")

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATIONS = REPO_ROOT / "data" / "stations.json"
DEFAULT_OUTPUT = REPO_ROOT / "data" / "oebb_station_lines.json"
STATE_VERSION = 1

# Three years: long closures are the rule, not the exception (the
# Stammstrecke is closed from 2026-09-07 to the end of October 2027, the
# Verbindungsbahn until the end of 2027). A line that really goes stays
# known that long; the stage-3 check then misses a finding, it never
# raises a false one.
RETENTION_DAYS = 3 * 365
SECOND_DATE_OFFSET_DAYS = 35
# (start time, duration in minutes)
SAMPLE_WINDOWS: tuple[tuple[str, int], ...] = (("060000", 180), ("150000", 180))
# Without ``maxJny`` HAFAS returned only ~50 departures (probe, 2026-09-25).
MAX_JOURNEYS = 400
# ÖBB rail product classes (hafas-client p/oebb/products.js): ICE/RJ, IC/EC,
# D/EN, R/REX, S-Bahn.
RAIL_CLASSES = 1 | 2 | 4 | 8 | 16 | 32 | 4096
# R/REX and S-Bahn: only these trains carry line numbers (S45, REX7, CJX9), so
# a stop counts only if it serves one of them. The Flughafen Wien bus terminal
# (pCls 1090: IC/EC, bus and 1024, no R/REX or S-Bahn) was chosen over the
# station in the run of 2026-09-26; its boards showed only "CAT by bus".
LOCAL_RAIL_CLASSES = 16 | 32
BOARD_MAX_BYTES = 5 * 1024 * 1024
PAUSE_SECONDS = 0.5
MAX_CONSECUTIVE_FAILURES = 5
# LocMatch candidates to choose from, and how far the chosen one may lie from
# the station. 800 m, not more: Karlsplatz lies 1.4 km from the Rennweg
# S-Bahn station, Stephansplatz 1.1 km from Wien Mitte.
LOC_MATCH_CANDIDATES = 8
MAX_MATCH_DISTANCE_M = 800.0
# HAFAS lists one station as several stops at the same coordinates, and only
# one of them may carry the departures: "Simmering (Wien)" and "Wien Simmering
# Bahnhof (U)", "Himberg b.Wien Bahnhof" and "Himberg b.Wien" (2026-09-26,
# each pair at the same distance, the first one with empty boards). When the
# chosen stop shows no line, up to two others within 50 m of it are tried.
# 50 m, not more: Quartier Belvedere lies 526 m from Wien Hbf.
SAME_PLACE_M = 50.0
MAX_SAME_PLACE_STOPS = 2
# HAFAS coordinates are integers in millionths of a degree.
_HAFAS_COORD_SCALE = 1_000_000.0
# Products shown per board when a station's boards yield no line.
MAX_LOGGED_PRODUCTS = 5
MAX_STATE_BYTES = 5 * 1024 * 1024

# ``at:obb:vor|S45:`` → ``S45``
_LINE_ID_RE = re.compile(r"\|([A-Z]{1,4}\d{1,3}):?$")
_LINE_TOKEN_RE = re.compile(r"^[A-Z]{1,4}\d{1,3}$")
_HAUPTBAHNHOF_RE = re.compile(r"\bHauptbahnhof\b")
# ÖBB's abbreviation. A stop found by the short name must carry it: Quartier
# Belvedere lies 526 m from Wien Hauptbahnhof, inside the match radius.
SHORT_FORM = "Hbf"


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


def _answer(payload: object) -> dict[str, Any] | None:
    """The ``res`` of a successful first service, ``None`` if it failed."""
    if not isinstance(payload, dict):
        return None
    services = payload.get("svcResL")
    if not isinstance(services, list) or not services or not isinstance(services[0], dict):
        return None
    service = services[0]
    if service.get("err") != "OK":
        return None
    res = service.get("res")
    return res if isinstance(res, dict) else {}


def lines_from_board(payload: object) -> set[str] | None:
    """The lines on a ``StationBoard`` answer, or ``None`` if it failed."""
    res = _answer(payload)
    if res is None:
        return None
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


@dataclass(frozen=True)
class RailLocation:
    """The HAFAS stop chosen for a station."""

    ext_id: str
    name: str
    classes: int
    distance_m: float
    latitude: float
    longitude: float


def _coordinates(location: Mapping[str, Any]) -> tuple[float, float] | None:
    """``(lat, lon)`` of a HAFAS location, ``None`` unless valid WGS84."""
    coords = location.get("crd")
    if not isinstance(coords, dict):
        return None
    x, y = coords.get("x"), coords.get("y")
    if not isinstance(x, int | float) or not isinstance(y, int | float):
        return None
    if isinstance(x, bool) or isinstance(y, bool):
        return None
    lat, lon = y / _HAFAS_COORD_SCALE, x / _HAFAS_COORD_SCALE
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def _rail_candidate(location: object) -> tuple[str, str, int, float, float] | None:
    """``(extId, name, pCls, lat, lon)`` of a candidate served by R/REX or S-Bahn, else ``None``."""
    if not isinstance(location, dict):
        return None
    ext_id = location.get("extId")
    classes = location.get("pCls")
    if not isinstance(ext_id, str) or not ext_id.strip():
        return None
    if not isinstance(classes, int) or isinstance(classes, bool) or not classes & LOCAL_RAIL_CLASSES:
        return None
    position = _coordinates(location)
    if position is None:
        return None
    return ext_id.strip(), str(location.get("name", "")), classes, *position


def rail_locations(
    payload: object, station: Station, name_part: str | None = None
) -> list[RailLocation]:
    """The ``LocMatch`` candidates served by R/REX or S-Bahn within 800 m, nearest first.

    The first run (2026-09-25) took HAFAS's top hit by name, which can be the
    tram or U-Bahn stop of the same name; a candidate now counts only if its
    product classes (``pCls``) include R/REX or S-Bahn
    (:data:`LOCAL_RAIL_CLASSES`). Any rail class was not enough: it let the
    Flughafen Wien bus terminal win over the station (2026-09-26). With
    *name_part*, a candidate must also carry it in its name. Ties keep
    HAFAS's order.
    """
    res = _answer(payload)
    if res is None or station.latitude is None or station.longitude is None:
        return []
    match = res.get("match")
    locations = match.get("locL") if isinstance(match, dict) else None
    found: list[RailLocation] = []
    for location in locations if isinstance(locations, list) else []:
        candidate = _rail_candidate(location)
        if candidate is None:
            continue
        ext_id, name, classes, lat, lon = candidate
        if name_part is not None and name_part.casefold() not in name.casefold():
            continue
        distance = calculate_distance_meters(station.latitude, station.longitude, lat, lon)
        if distance <= MAX_MATCH_DISTANCE_M:
            found.append(RailLocation(ext_id, name, classes, distance, lat, lon))
    return sorted(found, key=lambda location: location.distance_m)


def pick_rail_location(
    payload: object, station: Station, name_part: str | None = None
) -> RailLocation | None:
    """The nearest of :func:`rail_locations`, ``None`` if there is none."""
    locations = rail_locations(payload, station, name_part)
    return locations[0] if locations else None


def same_place_stops(payload: object, station: Station, ext_id: str) -> list[RailLocation]:
    """Other :func:`rail_locations` within :data:`SAME_PLACE_M` of the stop *ext_id*."""
    locations = rail_locations(payload, station)
    chosen = next((location for location in locations if location.ext_id == ext_id), None)
    if chosen is None:
        return []
    return [
        location
        for location in locations
        if location.ext_id != ext_id
        and calculate_distance_meters(chosen.latitude, chosen.longitude, location.latitude, location.longitude)
        <= SAME_PLACE_M
    ][:MAX_SAME_PLACE_STOPS]


def describe_candidates(payload: object, station: Station) -> str:
    """Every ``LocMatch`` candidate as ``name (extId, pCls, distance)``, for the log.

    Logged when no candidate qualifies (2026-09-26: nothing for "Wien
    Hauptbahnhof" and "Siebenhirten"), and when the chosen stop's boards
    yield no line (Simmering and Himberg, whose boards were empty).
    """
    res = _answer(payload)
    match = res.get("match") if res else None
    locations = match.get("locL") if isinstance(match, dict) else None
    parts: list[str] = []
    for location in locations if isinstance(locations, list) else []:
        if not isinstance(location, dict):
            continue
        position = _coordinates(location)
        distance = "?"
        if position is not None and station.latitude is not None and station.longitude is not None:
            meters = calculate_distance_meters(station.latitude, station.longitude, *position)
            distance = f"{round(meters)} m" if math.isfinite(meters) else "?"
        parts.append(
            f"{_clean(location.get('name'))} ({_clean(location.get('extId'))}, "
            f"pCls {_clean(location.get('pCls'))}, {distance})"
        )
    return "; ".join(parts) or "no candidates"


def board_summary(payload: object) -> str:
    """``jny 12, prod 3: S 45 [S/45/at:obb:vor|S45:], …`` for the log.

    Logged when a station's boards answer but yield no line (2026-09-26: Wien
    Mitte-Landstraße, Rennweg, Quartier Belvedere, Simmering, Himberg, all
    matched to a rail stop with the S-Bahn class).
    """
    res = _answer(payload)
    if res is None:
        return "failed"
    journeys = res.get("jnyL")
    common = res.get("common")
    products = common.get("prodL") if isinstance(common, dict) else None
    products = products if isinstance(products, list) else []
    shown: list[str] = []
    for item in products[:MAX_LOGGED_PRODUCTS]:
        if not isinstance(item, dict):
            continue
        context = item.get("prodCtx")
        context = context if isinstance(context, dict) else {}
        fields = "/".join(_clean(str(context.get(key, "")).strip()) for key in ("catOut", "line", "lineId"))
        shown.append(f"{_clean(item.get('name', ''))} [{fields}]")
    count = len(journeys) if isinstance(journeys, list) else 0
    return f"jny {count}, prod {len(products)}" + (": " + ", ".join(shown) if shown else "")


def short_name(name: str) -> str | None:
    """HAFAS's abbreviation of *name*, if it has one: ``Wien Hauptbahnhof`` → ``Wien Hbf``.

    Queried when the full name finds no rail stop. On 2026-09-26 ``LocMatch``
    for "Wien Hauptbahnhof" offered Meidling, Floridsdorf, Hütteldorf and the
    airport, but not the Hauptbahnhof, which ÖBB calls "Wien Hbf". The town
    stays in the query, and the 800 m radius around the station's own
    coordinates rules out every other Hauptbahnhof (St. Pölten lies 56 km
    away); a hit must also be named "… Hbf" (:data:`SHORT_FORM`).
    """
    short = _HAUPTBAHNHOF_RE.sub(SHORT_FORM, name)
    return short if short != name else None


def _is_resolved(entry: Mapping[str, Any]) -> bool:
    """Resolved by the current rule of :func:`pick_rail_location`.

    Ids from the first run lack ``hafas_classes``; the second run accepted any
    rail class. Such ids are resolved again once.
    """
    ext_id = entry.get("hafas_ext_id")
    classes = entry.get("hafas_classes")
    return (
        isinstance(ext_id, str)
        and bool(ext_id)
        and isinstance(classes, int)
        and not isinstance(classes, bool)
        and bool(classes & LOCAL_RAIL_CLASSES)
    )


Post = Callable[..., object]


class _FailureRun:
    """Counts failed requests in a row; five stop the run."""

    def __init__(self) -> None:
        self.in_a_row = 0

    def record(self, ok: bool) -> bool:
        """Record one request; ``True`` when the run must stop."""
        self.in_a_row = 0 if ok else self.in_a_row + 1
        return self.in_a_row >= MAX_CONSECUTIVE_FAILURES


def _call(post: Post, request: dict[str, object], pause: float, label: str, **kwargs: Any) -> object:
    """One paced request; ``None`` if it raised."""
    try:
        return post([request], **kwargs)
    except (HafasProfileError, requests.RequestException, ValueError) as exc:
        LOGGER.warning("%s failed for %s: %s", request.get("meth"), _clean(label), _clean(type(exc).__name__))
        return None
    finally:
        time.sleep(pause)


@dataclass
class RefreshResult:
    checked: int = 0
    failed: int = 0
    unresolved: int = 0
    aborted: bool = False


def _resolve(station: Station, entry: dict[str, Any], answer: object, query: str) -> bool:
    """Record the rail stop *answer* offers; ``False`` (and forget any old id) if none."""
    label = _clean(station.name if query == station.name else f"{station.name} (as {query})")
    location = pick_rail_location(answer, station, None if query == station.name else SHORT_FORM)
    if location is None:
        for key in ("hafas_ext_id", "hafas_name", "hafas_classes", "hafas_distance_m"):
            entry.pop(key, None)
        LOGGER.info(
            "No rail stop within %d m for %s; candidates: %s",
            int(MAX_MATCH_DISTANCE_M),
            label,
            describe_candidates(answer, station),
        )
        return False
    _store(entry, location)
    LOGGER.info("%s → %s", label, _describe(location))
    return True


def _store(entry: dict[str, Any], location: RailLocation) -> None:
    """Record *location* as the station's HAFAS stop."""
    entry["hafas_ext_id"] = location.ext_id
    entry["hafas_name"] = location.name
    entry["hafas_classes"] = location.classes
    entry["hafas_distance_m"] = round(location.distance_m)


def _describe(location: RailLocation) -> str:
    """``Wien Hbf (U) (1290401, pCls 6015, 292 m)`` for the log."""
    return (
        f"{_clean(location.name)} ({_clean(location.ext_id)}, "
        f"pCls {location.classes}, {round(location.distance_m)} m)"
    )


def _locate(
    station: Station, entry: dict[str, Any], post: Post, pause: float, failures: _FailureRun
) -> tuple[object, bool]:
    """``LocMatch`` for *station*, then for its :func:`short_name`; the stop goes into *entry*.

    Returns the last answer and whether the run must stop. A failed request
    leaves *entry* as it was and is not followed by the short name.
    """
    answer: object = None
    for query in (station.name, short_name(station.name)):
        if query is None:
            break
        answer = _call(post, loc_match_request(query, LOC_MATCH_CANDIDATES), pause, station.name)
        answered = _answer(answer) is not None
        if failures.record(answered):
            return answer, True
        if not answered or _resolve(station, entry, answer, query):
            break
    return answer, False


@dataclass
class _Boards:
    """What the sample boards of one station showed."""

    seen: set[str] = field(default_factory=set)
    answered: bool = False
    aborted: bool = False
    summaries: list[str] = field(default_factory=list)


def _try_same_place(
    station: Station,
    entry: dict[str, Any],
    answer: object,
    boards: _Boards,
    dates: Sequence[date],
    post: Post,
    pause: float,
    failures: _FailureRun,
) -> tuple[_Boards, list[str]]:
    """Boards without a line: try the other stops at the chosen stop's place.

    The first one whose boards show a line goes into *entry* and its boards
    are returned; otherwise *boards*. Also returns the stops tried in vain.
    """
    tried: list[str] = []
    for other in same_place_stops(answer, station, entry["hafas_ext_id"]):
        trial = _sample_boards(station, other.ext_id, dates, post, pause, failures)
        if trial.aborted:
            return trial, tried
        if trial.seen:
            LOGGER.info(
                "%s: no line at %s (%s), lines at %s",
                _clean(station.name),
                _clean(entry.get("hafas_name")),
                _clean(entry["hafas_ext_id"]),
                _describe(other),
            )
            _store(entry, other)
            return trial, tried
        tried.append(f"{_clean(other.name)} ({_clean(other.ext_id)})")
    return boards, tried


def _sample_boards(
    station: Station, ext_id: str, dates: Sequence[date], post: Post, pause: float, failures: _FailureRun
) -> _Boards:
    """Request every sample window of *station*; stop early when the run must stop."""
    boards = _Boards()
    for day, (start, minutes) in product(dates, SAMPLE_WINDOWS):
        board = _call(
            post,
            board_request(ext_id, day, start, minutes),
            pause,
            station.name,
            max_bytes=BOARD_MAX_BYTES,
        )
        lines = lines_from_board(board)
        boards.summaries.append(f"{day:%d.%m.} {start[:2]}h {board_summary(board)}")
        if failures.record(lines is not None):
            boards.aborted = True
            break
        if lines is not None:
            boards.answered = True
            boards.seen |= lines
    return boards


def _log_no_line(station: Station, answer: object, boards: _Boards, tried: Sequence[str]) -> None:
    """Board summaries, and the candidates when this run looked the station up."""
    details = "" if _answer(answer) is None else f"; candidates: {describe_candidates(answer, station)}"
    if tried:
        details += f"; also without a line: {', '.join(tried)}"
    LOGGER.info("No line on the boards of %s: %s%s", _clean(station.name), " | ".join(boards.summaries), details)


def refresh(
    stations: Sequence[Station],
    state: dict[str, Any],
    today: date,
    *,
    post: Post = post_mgate,
    pause: float = PAUSE_SECONDS,
) -> RefreshResult:
    """Update *state* (``{bst_id: entry}``) in place for *stations*."""
    result = RefreshResult()
    failures = _FailureRun()
    dates = sample_dates(today)
    in_scope = {station.bst_id for station in stations}
    for stale in [key for key in state if key not in in_scope]:
        del state[stale]

    for station in stations:
        entry = state.setdefault(station.bst_id, {"lines": {}})
        entry["name"] = station.name
        answer: object = None
        # A station without a line is looked up again: the log then lists
        # what HAFAS offers, and a better stop is picked up once it exists.
        if not _is_resolved(entry) or not entry.get("lines"):
            answer, stop = _locate(station, entry, post, pause, failures)
            if stop:
                result.aborted = True
                break
            if not _is_resolved(entry):
                if _answer(answer) is None:
                    result.failed += 1
                else:
                    result.unresolved += 1
                continue

        boards = _sample_boards(station, entry["hafas_ext_id"], dates, post, pause, failures)
        tried: list[str] = []
        if not boards.aborted and boards.answered and not boards.seen:
            boards, tried = _try_same_place(station, entry, answer, boards, dates, post, pause, failures)
        if boards.aborted:
            result.aborted = True
            break
        if not boards.answered:
            result.failed += 1
            continue
        if not boards.seen:
            _log_no_line(station, answer, boards, tried)
        known = entry.get("lines")
        entry["lines"] = merge_lines(known if isinstance(known, dict) else {}, boards.seen, today)
        entry["checked"] = today.isoformat()
        result.checked += 1
    if result.aborted:
        LOGGER.error(
            "%d HAFAS failures in a row; stopped, keeping what was collected",
            MAX_CONSECUTIVE_FAILURES,
        )
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
