"""Common shape of a station candidate from a coordinate source.

The OSM Overpass client produces :class:`Place` objects and
:func:`src.places.merge.merge_places` merges them into the station
directory. (Until 2026-10-16 this class lived in the Google Places client,
which is removed.)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Place:
    """A station candidate: id, name, coordinate and type tags."""

    place_id: str
    name: str
    latitude: float
    longitude: float
    types: list[str]
    formatted_address: str | None
