"""Parsing and selection layer of ``scripts/update_oebb_station_lines.py``.

Station selection, sample dates, StationBoard and LocMatch parsing, log
formatting. Moved verbatim from the script; the logger keeps its name
``oebb_station_lines``. A test that wants to patch a constant or function
used here must patch it on this module, not on the script.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from logging import getLogger
from typing import Any

from src.utils.geo import calculate_distance_meters
from src.utils.logging import sanitize_log_arg

LOGGER = getLogger("oebb_station_lines")
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
