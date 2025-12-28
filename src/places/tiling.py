"""Helpers for configuring search tiles for the Google Places API."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, List, Mapping, cast

__all__ = ["Tile", "load_tiles_from_env", "load_tiles_from_file", "iter_tiles"]


@dataclass(frozen=True)
class Tile:
    """A circular search tile defined by latitude and longitude."""

    latitude: float
    longitude: float


_DEFAULT_TILE = Tile(latitude=48.208174, longitude=16.373819)
MAX_TILE_COUNT = 200


def _validate_tile_count(count: int) -> None:
    # Security: prevent unbounded tile lists from triggering excessive API calls/DoS.
    if count > MAX_TILE_COUNT:
        raise ValueError(
            f"Tile configuration exceeds the limit of {MAX_TILE_COUNT} entries."
        )


def _coerce_coordinate(raw: Mapping[str, object], key: str) -> float:
    value = raw.get(key)
    if isinstance(value, (float, int)):
        return float(value)
    if isinstance(value, str):
        return float(value)
    raise TypeError(f"Invalid {key!r} value in tile specification: {value!r}")


def _parse_tiles(raw_tiles: Iterable[Mapping[str, object]]) -> List[Tile]:
    tiles: List[Tile] = []
    for raw in raw_tiles:
        try:
            lat = _coerce_coordinate(raw, "lat")
            lng = _coerce_coordinate(raw, "lng")
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid tile specification: {raw!r}") from exc
        tiles.append(Tile(latitude=lat, longitude=lng))
    return tiles


def load_tiles_from_env(raw_value: str | None) -> List[Tile]:
    """Parse ``raw_value`` from ``PLACES_TILES`` into :class:`Tile` objects."""

    if not raw_value:
        return [_DEFAULT_TILE]

    data = json.loads(raw_value)
    if not isinstance(data, list):
        raise ValueError("PLACES_TILES must encode a list of objects")
    _validate_tile_count(len(data))
    return _parse_tiles(cast(Iterable[Mapping[str, object]], data))


def load_tiles_from_file(path: Path) -> List[Tile]:
    """Load tile configuration from ``path``."""

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Tile file must contain a list of tile objects")
    _validate_tile_count(len(data))
    return _parse_tiles(cast(Iterable[Mapping[str, object]], data))


def iter_tiles(tiles: Iterable[Tile]) -> Iterator[Tile]:
    """Yield tiles from ``tiles`` ensuring there is at least one tile."""

    materialised = list(tiles)
    if not materialised:
        yield _DEFAULT_TILE
        return
    yield from materialised
