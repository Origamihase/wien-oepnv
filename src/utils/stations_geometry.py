"""Point-in-polygon geometry for the Vienna city boundary.

Moved verbatim from ``src/utils/stations.py`` (700-line rule, 2026-11-02);
the public facade stays ``src.utils.stations``. Lowest layer: this module
must not import the facade or the other ``stations_*`` parts.
"""

from __future__ import annotations

from itertools import pairwise


Coordinate = tuple[float, float]
Ring = tuple[Coordinate, ...]
Polygon = tuple[Ring, ...]


def _point_on_segment(
    lat: float,
    lon: float,
    start: Coordinate,
    end: Coordinate,
    tolerance: float = 1e-9,
) -> bool:
    """Return ``True`` if *lat*, *lon* lies on the line segment (*start*, *end*)."""

    lat1, lon1 = start
    lat2, lon2 = end
    if abs(lat - lat1) <= tolerance and abs(lon - lon1) <= tolerance:
        return True
    if abs(lat - lat2) <= tolerance and abs(lon - lon2) <= tolerance:
        return True
    if not (
        min(lat1, lat2) - tolerance <= lat <= max(lat1, lat2) + tolerance
        and min(lon1, lon2) - tolerance <= lon <= max(lon1, lon2) + tolerance
    ):
        return False
    dx_segment = lon2 - lon1
    dy_segment = lat2 - lat1
    dx_point = lon - lon1
    dy_point = lat - lat1
    cross = dx_point * dy_segment - dy_point * dx_segment
    if abs(cross) > tolerance:
        return False
    dot = dx_point * dx_segment + dy_point * dy_segment
    length_sq = dx_segment * dx_segment + dy_segment * dy_segment
    return -tolerance <= dot <= length_sq + tolerance


def _point_in_ring(lat: float, lon: float, ring: Ring) -> bool:
    """Return ``True`` if *lat*, *lon* is inside the polygon *ring*."""

    if len(ring) < 3:
        return False
    inside = False
    for start, end in pairwise((*ring, ring[0])):
        if _point_on_segment(lat, lon, start, end):
            return True
        lat1, lon1 = start
        lat2, lon2 = end
        if (lat1 > lat) != (lat2 > lat):
            try:
                intersect_lon = lon1 + (lon2 - lon1) * (lat - lat1) / (lat2 - lat1)
            except ZeroDivisionError:
                intersect_lon = lon1
            if lon < intersect_lon:
                inside = not inside
    return inside


def _point_in_polygon(lat: float, lon: float, rings: Polygon) -> bool:
    """Return ``True`` if *lat*, *lon* lies within the polygon defined by *rings*."""

    if not rings:
        return False
    if not _point_in_ring(lat, lon, rings[0]):
        return False
    for hole in rings[1:]:
        if _point_in_ring(lat, lon, hole):
            return False
    return True
