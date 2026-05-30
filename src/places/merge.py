"""Merge helpers for integrating Google Places data into stations."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict, cast
from collections.abc import Iterable, MutableMapping, Sequence

from ..utils.files import (
    _reject_non_finite_constant,
    _reject_non_finite_float,
    atomic_write,
)
from ..utils.serialize import scrub_trojan_source_primitives
from ..utils.stations import MAX_STATIONS_FILE_BYTES

from .client import Place
from .normalize import haversine_m, normalize_name

__all__ = [
    "BoundingBox",
    "MergeConfig",
    "MergeOutcome",
    "StationEntry",
    "load_stations",
    "write_stations",
    "merge_places",
]

class StationEntry(TypedDict, total=False):
    bst_id: str
    bst_code: str
    name: str
    in_vienna: bool
    pendler: bool
    source: str
    aliases: list[str]
    latitude: float
    longitude: float
    _google_place_id: str
    _types: list[str]
    _formatted_address: str


@dataclass(frozen=True)
class BoundingBox:
    min_lat: float
    min_lng: float
    max_lat: float
    max_lng: float

    def contains(self, lat: float, lng: float) -> bool:
        return self.min_lat <= lat <= self.max_lat and self.min_lng <= lng <= self.max_lng


@dataclass(frozen=True)
class MergeConfig:
    max_distance_m: float
    bounding_box: BoundingBox | None


@dataclass
class MergeOutcome:
    stations: list[StationEntry]
    new_entries: list[StationEntry]
    updated_entries: list[StationEntry]
    skipped_places: list[Place]


def load_stations(path: Path) -> list[StationEntry]:
    if not path.exists():
        return []
    # Security: byte-size cap (see MAX_STATIONS_FILE_BYTES) defeats the
    # wide-but-flat size-bomb attack that the depth-bomb catch below does
    # NOT cover. ``path.read_text`` buffers the entire file before parsing,
    # so a 1 GiB stations file would allocate >1 GiB before ``json.loads``
    # ever runs. ``MemoryError`` is a ``BaseException`` that propagates
    # past the ``except (json.JSONDecodeError, RecursionError)`` handler.
    # Open first, then ``os.fstat`` the descriptor — closes the TOCTOU
    # between ``stat`` and ``read_text`` that lets an attacker swap the
    # inode between the two syscalls. ``read(MAX_STATIONS_FILE_BYTES + 1)``
    # defends against zero-st_size special files.
    try:
        with path.open("rb") as handle:
            file_size = os.fstat(handle.fileno()).st_size
            if file_size > MAX_STATIONS_FILE_BYTES:
                raise ValueError(
                    f"stations file too large (> {MAX_STATIONS_FILE_BYTES} bytes)"
                )
            content_bytes = handle.read(MAX_STATIONS_FILE_BYTES + 1)
            if len(content_bytes) > MAX_STATIONS_FILE_BYTES:
                raise ValueError(
                    f"stations file too large (> {MAX_STATIONS_FILE_BYTES} bytes)"
                )
    except OSError as exc:
        raise ValueError("stations file is not readable") from exc
    # Security: ``RecursionError`` covers JSON depth-bomb attacks in an
    # operator-supplied stations file. ``json.loads`` raises
    # ``RecursionError`` (NOT a subclass of ``json.JSONDecodeError``) on a
    # deeply-nested but well-formed payload. Without this catch the error
    # propagates out of ``load_stations`` and crashes the Google Places
    # merge step in ``update_station_directory.py``.
    try:
        # Security (reader-side non-finite literal defence): mirrors
        # the writer-side ``allow_nan=False`` pin at
        # :func:`write_stations` (Round 1485 — the CANONICAL coordinate
        # writer). A planted ``NaN`` / ``Infinity`` / ``-Infinity`` /
        # ``1e1000`` in an operator-supplied ``data/stations.json``
        # (compromised CI runner, partial flush + power loss, hostile PR
        # landing a tampered fixture, supply-chain attack via the OSM /
        # Google Places merge step) would otherwise propagate as
        # ``float('nan')`` / ``float('inf')`` into the merge pipeline.
        # While ``filter_complete_places`` and ``_parse_place`` already
        # reject non-finite coordinates at the API ingest layer, the
        # on-disk file read here is a SEPARATE attacker position (the
        # disk-write boundary, downstream of the API ingest). The
        # defence-in-depth ``parse_constant`` + ``parse_float`` hooks
        # close the parse-time entry point so the in-memory list never
        # contains a non-finite float regardless of how the on-disk
        # bytes got there. Hook raises ``json.JSONDecodeError`` caught
        # by the surrounding except tuple.
        raw_data = json.loads(
            content_bytes,
            parse_constant=_reject_non_finite_constant,
            parse_float=_reject_non_finite_float,
        )
    except (json.JSONDecodeError, RecursionError, UnicodeDecodeError) as exc:
        raise ValueError("stations file is not valid JSON") from exc

    if isinstance(raw_data, list):
        data = raw_data
    elif isinstance(raw_data, dict) and isinstance(raw_data.get("stations"), list):
        data = raw_data["stations"]
    else:
        raise ValueError("stations file must contain a list or wrapped object")

    stations: list[StationEntry] = []
    for raw in data:
        if not isinstance(raw, MutableMapping):
            raise ValueError("stations entries must be objects")
        stations.append(cast(StationEntry, deepcopy(raw)))
    # Security (Trojan-Source / BiDi-Mark Drift Round 13, defence-in-depth at
    # the read boundary): retroactively scrub the canonical CVE-2021-42574
    # attack-byte union from any historic poisoned ``data/stations.json``
    # (planted before this fix, surviving from a corrupted previous run, or
    # written by a future bypass of ``write_stations``'s ingestion-boundary
    # scrubber). Mirrors the write-side defence so the in-memory payload
    # handed to the merge / validation step cannot carry raw BiDi marks
    # regardless of how the on-disk bytes got there. See
    # ``src/utils/serialize.py:scrub_trojan_source_primitives`` for the
    # canonical attack-byte union.
    scrubbed = scrub_trojan_source_primitives(stations)
    if isinstance(scrubbed, list):
        return scrubbed
    return stations


def write_stations(path: Path, stations: Sequence[StationEntry]) -> None:
    # Security (Trojan-Source / BiDi-Mark Drift Round 13, ingestion-boundary
    # defence): strip the canonical CVE-2021-42574 attack-byte union (BiDi
    # formatting controls, BiDi isolates, zero-width primitives + LRM/RLM/ALM,
    # Unicode line / paragraph separators, the BOM / ZWNBSP, and the 8-bit
    # C1 terminal-escape primitives) from every reachable string in the
    # incoming stations BEFORE ``json.dumps``. ``data/stations.json`` is
    # committed to ``main`` by the weekly ``update-stations.yml`` cron job
    # (the OSM-first / Google-Places-fallback runs as a step there) and
    # rendered via ``cat`` / ``less`` / the GitHub web UI / IDE preview.
    # ``ensure_ascii=False`` is preserved at the writer below so legitimate
    # German station names (umlauts ä/ö/ü/Ä/Ö/Ü + sharp s ß + every other
    # safe Unicode code point) stay compact in the weekly commit diff;
    # pairing it with the scrubber rejects the canonical attack-byte union
    # before it reaches the serialiser. See
    # ``src/utils/serialize.py:scrub_trojan_source_primitives`` for the
    # canonical attack-byte union and the scrub-and-drop semantics rationale.
    scrubbed = scrub_trojan_source_primitives(list(stations))
    serialisable = scrubbed if isinstance(scrubbed, list) else list(stations)
    # Security (Coordinate finite/range drift, writer-level defence-in-depth):
    # ``allow_nan=False`` makes ``json.dumps`` raise ``ValueError`` on any
    # ``NaN`` / ``+Inf`` / ``-Inf`` float in the payload — RFC 8259 forbids
    # those non-standard literals, and every strict downstream consumer
    # (``JSON.parse``, ``serde_json``, ``encoding/json``) refuses them. A
    # parser-level finite floor exists on every current ingest tier (OSM,
    # HAFAS, Google Places); this pin surfaces a future bypass as a loud
    # failure at write time rather than silently corrupting the committed
    # ``data/stations.json`` artefact.
    payload = json.dumps(
        {"stations": serialisable},
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    )
    # Security: use atomic_write to avoid partial writes on crashes/power loss.
    with atomic_write(path, mode="w", encoding="utf-8", permissions=0o644) as handle:
        handle.write(payload + "\n")


def merge_places(
    existing: Sequence[StationEntry],
    places: Iterable[Place],
    config: MergeConfig,
) -> MergeOutcome:
    stations = [deepcopy(entry) for entry in existing]
    new_entries: list[StationEntry] = []
    updated_entries: list[StationEntry] = []
    skipped_places: list[Place] = []

    for station in stations:
        if "aliases" not in station:
            station["aliases"] = []

    for place in places:
        result = _find_matching_station(stations, place, config.max_distance_m)
        if result is not None:
            station, matched_by_name = result
            if _update_station(station, place, config, matched_by_name):
                updated_entries.append(station)
            else:
                skipped_places.append(place)
            continue
        new_station = _create_station(place, config)
        stations.append(new_station)
        new_entries.append(new_station)

    stations = _sorted_stations(stations)
    return MergeOutcome(
        stations=stations,
        new_entries=new_entries,
        updated_entries=updated_entries,
        skipped_places=skipped_places,
    )


def _sorted_stations(stations: Sequence[StationEntry]) -> list[StationEntry]:
    # ``.get(key, default)`` only returns the default when the key is
    # ABSENT — a present-but-``null`` ``_google_place_id`` (operator-
    # edited / legacy / tampered ``data/stations.json``) returns ``None``.
    # Two same-normalized-name entries where one carries ``null`` and the
    # other a string id would then crash sorted() with
    # ``TypeError: '<' not supported between instances of 'NoneType' and 'str'``.
    # Coerce ``None`` to ``""`` so the tuple comparison stays string-only,
    # mirroring the ``str()`` coercion already used on the ``name`` key.
    return sorted(
        stations,
        key=lambda entry: (
            normalize_name(str(entry.get("name", ""))),
            entry.get("_google_place_id") or "",
        ),
    )


def _find_matching_station(
    stations: Sequence[StationEntry],
    place: Place,
    max_distance_m: float,
) -> tuple[StationEntry, bool] | None:
    norm = normalize_name(place.name)
    for station in stations:
        name = station.get("name")
        if isinstance(name, str) and normalize_name(name) == norm:
            return station, True

    # Distance fallback: bind the place to the *nearest* station within
    # ``max_distance_m`` rather than the first one encountered. In dense
    # areas several stops can sit within the radius (e.g. a U-Bahn access
    # and an adjacent tram stop); returning the first in iteration order
    # could attach the place to the wrong station and overwrite its
    # coordinates. Mirrors ``stations.nearest_rail_station``. Ties keep the
    # earlier station (strict ``<``).
    best: StationEntry | None = None
    best_distance = float("inf")
    for station in stations:
        lat = station.get("latitude")
        lng = station.get("longitude")
        if not (isinstance(lat, float | int) and isinstance(lng, float | int)):
            continue
        # Defence-in-depth (Coordinate finite/range drift — disk-read side):
        # ``load_stations`` already rejects non-finite literals
        # (``NaN``/``Inf``/``1e1000``) at parse time, but a finite-yet-
        # OUT-OF-WGS84-range value (``latitude: 999.0`` from a hand edit /
        # legacy backup / planted file) still slips through and the
        # ``haversine_m`` call below would raise ``ValueError`` for the
        # [-90,90] / [-180,180] bounds — propagating out of
        # ``merge_places`` and crashing the Google Places station-
        # directory update. Skipping a single corrupt entry mirrors the
        # non-numeric skip above and keeps the merge running.
        lat_f, lng_f = float(lat), float(lng)
        if not (-90.0 <= lat_f <= 90.0 and -180.0 <= lng_f <= 180.0):
            continue
        distance = haversine_m(lat_f, lng_f, place.latitude, place.longitude)
        if distance <= max_distance_m and distance < best_distance:
            best = station
            best_distance = distance
    if best is not None:
        return best, False
    return None


def _ensure_source_set(station: StationEntry) -> set[str]:
    source = station.get("source")
    if source is None:
        return set()

    return {s.strip() for s in str(source).split(",") if s.strip()}


def _update_station(
    station: StationEntry,
    place: Place,
    config: MergeConfig,
    matched_by_name: bool,
) -> bool:
    changed = False

    sources = _ensure_source_set(station)
    if "google_places" not in sources:
        sources.add("google_places")
        # Ensure 'source' is saved as a comma-separated string
        station["source"] = ",".join(sorted(sources))
        changed = True
    else:
        # Also fix existing format just in case it wasn't string
        new_source_str = ",".join(sorted(sources))
        if station.get("source") != new_source_str:
            station["source"] = new_source_str
            changed = True

    existing_place_id = station.get("_google_place_id")
    if existing_place_id != place.place_id and (
        existing_place_id is None or matched_by_name or not isinstance(existing_place_id, str)
    ):
        station["_google_place_id"] = place.place_id
        changed = True

    if station.get("latitude") != place.latitude:
        station["latitude"] = place.latitude
        changed = True
    if station.get("longitude") != place.longitude:
        station["longitude"] = place.longitude
        changed = True

    if place.types:
        if station.get("_types") != place.types:
            station["_types"] = list(place.types)
            changed = True
    if place.formatted_address:
        if station.get("_formatted_address") != place.formatted_address:
            station["_formatted_address"] = place.formatted_address
            changed = True

    if "in_vienna" not in station:
        station["in_vienna"] = _infer_in_vienna(place, config.bounding_box)
        changed = True

    if "pendler" not in station:
        station["pendler"] = False
        changed = True

    if "aliases" not in station:
        station["aliases"] = []
        changed = True

    return changed


def _create_station(place: Place, config: MergeConfig) -> StationEntry:
    station: StationEntry = {
        "name": place.name,
        "source": "google_places",
        "aliases": [],
        "latitude": place.latitude,
        "longitude": place.longitude,
        "pendler": False,
        "_google_place_id": place.place_id,
    }
    if place.types:
        station["_types"] = list(place.types)
    if place.formatted_address:
        station["_formatted_address"] = place.formatted_address
    station["in_vienna"] = _infer_in_vienna(place, config.bounding_box)
    return station


def _infer_in_vienna(place: Place, bounding_box: BoundingBox | None) -> bool:
    address = place.formatted_address
    if isinstance(address, str):
        lowered = address.casefold()
        if "wien" in lowered or "vienna" in lowered:
            return True
    if bounding_box is not None:
        if bounding_box.contains(place.latitude, place.longitude):
            return True
    return False
