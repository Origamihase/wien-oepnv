"""Network-driving refresh and state I/O of ``scripts/update_oebb_station_lines.py``.

Moved verbatim from the script. ``refresh`` binds ``post=post_mgate`` when it
is defined; a test that wants to patch ``_call``, ``time.sleep`` or the
helpers must patch them on this module, not on the script.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from itertools import product
from pathlib import Path
from typing import Any

import requests

from src.oebb_lines.hafas import (
    BOARD_MAX_BYTES,
    LOC_MATCH_CANDIDATES,
    LOCAL_RAIL_CLASSES,
    LOGGER,
    MAX_CONSECUTIVE_FAILURES,
    MAX_MATCH_DISTANCE_M,
    MAX_STATE_BYTES,
    PAUSE_SECONDS,
    RETENTION_DAYS,
    SAMPLE_WINDOWS,
    SHORT_FORM,
    STATE_VERSION,
    RailLocation,
    Station,
    _answer,
    _clean,
    board_request,
    board_summary,
    describe_candidates,
    lines_from_board,
    merge_lines,
    pick_rail_location,
    same_place_stops,
    sample_dates,
    short_name,
)
from src.places.hafas_client import HafasProfileError, loc_match_request, post_mgate
from src.utils.files import atomic_write, read_capped_json
from src.utils.serialize import scrub_trojan_source_primitives


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
