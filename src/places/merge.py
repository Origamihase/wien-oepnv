"""Merge helpers for integrating Google Places data into stations."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, MutableMapping, Optional, Sequence, Tuple, TypedDict, cast

try:  # pragma: no cover
    from utils.files import atomic_write
except ModuleNotFoundError:  # pragma: no cover
    from ..utils.files import atomic_write

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
    bst_id: int
    bst_code: str
    name: str
    in_vienna: bool
    pendler: bool
    source: str | List[str]
    aliases: List[str]
    _google_place_id: str
    _lat: float
    _lng: float
    _types: List[str]
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
    bounding_box: Optional[BoundingBox]


@dataclass
class MergeOutcome:
    stations: List[StationEntry]
    new_entries: List[StationEntry]
    updated_entries: List[StationEntry]
    skipped_places: List[Place]


def load_stations(path: Path) -> List[StationEntry]:
    if not path.exists():
        return []
    content = path.read_text(encoding="utf-8")
    raw_data = json.loads(content)

    if isinstance(raw_data, list):
        data = raw_data
    elif isinstance(raw_data, dict) and isinstance(raw_data.get("stations"), list):
        data = raw_data["stations"]
    else:
        raise ValueError("stations file must contain a list or wrapped object")

    stations: List[StationEntry] = []
    for raw in data:
        if not isinstance(raw, MutableMapping):
            raise ValueError("stations entries must be objects")
        stations.append(cast(StationEntry, deepcopy(raw)))
    return stations


def write_stations(path: Path, stations: Sequence[StationEntry]) -> None:
    serialisable = list(stations)
    payload = json.dumps({"stations": serialisable}, ensure_ascii=False, indent=2, sort_keys=True)
    # Security: use atomic_write to avoid partial writes on crashes/power loss.
    with atomic_write(path, mode="w", encoding="utf-8", permissions=0o644) as handle:
        handle.write(payload + "\n")


def merge_places(
    existing: Sequence[StationEntry],
    places: Iterable[Place],
    config: MergeConfig,
) -> MergeOutcome:
    stations = [deepcopy(entry) for entry in existing]
    new_entries: List[StationEntry] = []
    updated_entries: List[StationEntry] = []
    skipped_places: List[Place] = []

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


def _sorted_stations(stations: Sequence[StationEntry]) -> List[StationEntry]:
    return sorted(
        stations,
        key=lambda entry: (
            normalize_name(str(entry.get("name", ""))),
            entry.get("_google_place_id", ""),
        ),
    )


def _find_matching_station(
    stations: Sequence[StationEntry],
    place: Place,
    max_distance_m: float,
) -> Optional[Tuple[StationEntry, bool]]:
    norm = normalize_name(place.name)
    for station in stations:
        name = station.get("name")
        if isinstance(name, str) and normalize_name(name) == norm:
            return station, True

    for station in stations:
        lat = station.get("_lat")
        lng = station.get("_lng")
        if isinstance(lat, (float, int)) and isinstance(lng, (float, int)):
            distance = haversine_m(float(lat), float(lng), place.latitude, place.longitude)
            if distance <= max_distance_m:
                return station, False
    return None


def _ensure_source_list(station: StationEntry) -> List[str]:
    source = station.get("source")
    if source is None:
        result: List[str] = []
        station["source"] = result
        return result
    if isinstance(source, list):
        return source
    if isinstance(source, str):
        values = [source]
        station["source"] = values
        return values
    values = [str(source)]
    station["source"] = values
    return values


def _update_station(
    station: StationEntry,
    place: Place,
    config: MergeConfig,
    matched_by_name: bool,
) -> bool:
    changed = False

    sources = _ensure_source_list(station)
    if "google_places" not in sources:
        sources.append("google_places")
        changed = True

    existing_place_id = station.get("_google_place_id")
    if existing_place_id != place.place_id and (
        existing_place_id is None or matched_by_name or not isinstance(existing_place_id, str)
    ):
        station["_google_place_id"] = place.place_id
        changed = True

    if station.get("_lat") != place.latitude:
        station["_lat"] = place.latitude
        changed = True
    if station.get("_lng") != place.longitude:
        station["_lng"] = place.longitude
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

    if "aliases" not in station:
        station["aliases"] = []
        changed = True

    return changed


def _create_station(place: Place, config: MergeConfig) -> StationEntry:
    station: StationEntry = {
        "name": place.name,
        "source": ["google_places"],
        "aliases": [],
        "_google_place_id": place.place_id,
        "_lat": place.latitude,
        "_lng": place.longitude,
    }
    if place.types:
        station["_types"] = list(place.types)
    if place.formatted_address:
        station["_formatted_address"] = place.formatted_address
    station["in_vienna"] = _infer_in_vienna(place, config.bounding_box)
    return station


def _infer_in_vienna(place: Place, bounding_box: Optional[BoundingBox]) -> bool:
    address = place.formatted_address
    if isinstance(address, str):
        lowered = address.casefold()
        if "wien" in lowered or "vienna" in lowered:
            return True
    if bounding_box is not None:
        if bounding_box.contains(place.latitude, place.longitude):
            return True
    return False
