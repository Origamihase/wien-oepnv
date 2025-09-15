"""Helpers for loading metadata from :mod:`data.stations.json`."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable, Dict, Iterable, Tuple

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_PATH = BASE_DIR / "data" / "stations.json"


@dataclass(frozen=True)
class Station:
    """Station entry as stored in :file:`data/stations.json`."""

    bst_id: int
    bst_code: str
    name: str
    in_vienna: bool


def _parse_station(raw: dict[str, object]) -> Station:
    try:
        bst_id = int(raw["bst_id"])
        bst_code = str(raw["bst_code"])
        name = str(raw["name"])
        in_vienna = bool(raw.get("in_vienna", False))
    except (KeyError, TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise ValueError(f"Invalid station entry: {raw!r}") from exc
    return Station(bst_id=bst_id, bst_code=bst_code, name=name, in_vienna=in_vienna)


@lru_cache(maxsize=1)
def load_stations() -> Tuple[Station, ...]:
    """Return all stations from :file:`data/stations.json`."""

    with DATA_PATH.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):  # pragma: no cover - defensive
        raise ValueError("stations.json must contain a list")
    stations = tuple(_parse_station(item) for item in payload)
    return stations


def iter_stations() -> Iterable[Station]:
    """Iterate over all station entries."""

    return load_stations()


@lru_cache(maxsize=1)
def stations_by_id() -> Dict[int, Station]:
    """Return a mapping from BST-ID to station metadata."""

    return {station.bst_id: station for station in load_stations()}


def normalized_name_index(normalize: Callable[[str], str], *, only_vienna: bool = False) -> Dict[str, Station]:
    """Build an index keyed by ``normalize(station.name)``.

    Args:
        normalize: Function used to normalize station names.
        only_vienna: When :data:`True`, limit results to stations marked as
            ``in_vienna``.
    """

    index: Dict[str, Station] = {}
    for station in load_stations():
        if only_vienna and not station.in_vienna:
            continue
        key = normalize(station.name)
        if not key:
            continue
        index.setdefault(key, station)
    return index


def vienna_bst_ids() -> set[int]:
    """Return the BST-IDs for stations located in Vienna."""

    return {station.bst_id for station in load_stations() if station.in_vienna}
