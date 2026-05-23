"""Relevance policy for the Stadt-Wien construction-site provider.

The upstream WFS feed (``ogdwien:BAUSTELLEOGD``) lists *every* road
construction site in Vienna — the overwhelming majority of which never
touch public transport. To keep the feed a focused ÖPNV signal we admit
a construction site only when it sits at (or right next to) a rail
*Bahnhof*: a Wien station or a Pendlerbahnhof from the curated station
directory. A lane closure on the forecourt of Wien Floridsdorf is worth
surfacing; one in a back courtyard 2 km from any station is not.

The decision is purely geographic — it compares the construction site's
coordinate against the rail-station coordinates already maintained in
``data/stations.json`` (see :func:`src.utils.stations.nearest_rail_station`).
There is no free-text matching, so there is no ReDoS surface and no
ambiguity from street names that merely echo a station name.
"""
from __future__ import annotations

import math
import os
from typing import Any, Final

from ..utils.stations import nearest_rail_station

__all__ = [
    "DEFAULT_STATION_RADIUS_M",
    "is_transit_relevant",
    "relevant_station",
]

#: Default proximity (in metres) between a construction site and a rail
#: Bahnhof for the site to count as ÖPNV-relevant. 150 m mirrors the
#: project's existing "effectively at the station" threshold
#: (:data:`src.utils.geo.STATION_DRIFT_TOLERANCE_METERS`): a closure
#: within 150 m of a Bahnhof plausibly affects access to it, while the
#: tight radius keeps unrelated road works out of the feed.
DEFAULT_STATION_RADIUS_M: Final = 150.0

# Operator override bounds. The upper bound stops anyone widening the
# radius until the filter re-floods the feed it exists to protect; the
# lower bound keeps the match meaningful (a sub-25 m radius would drop
# legitimate forecourt closures over GPS jitter alone).
_MIN_STATION_RADIUS_M: Final = 25.0
_MAX_STATION_RADIUS_M: Final = 2_000.0

_RADIUS_ENV: Final = "BAUSTELLEN_STATION_RADIUS_M"


def _resolve_radius_m() -> float:
    """Return the proximity radius, honouring the clamped env override."""

    raw = os.getenv(_RADIUS_ENV, "")
    if not raw.strip():
        return DEFAULT_STATION_RADIUS_M
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_STATION_RADIUS_M
    if not math.isfinite(value):
        return DEFAULT_STATION_RADIUS_M
    return min(max(value, _MIN_STATION_RADIUS_M), _MAX_STATION_RADIUS_M)


def relevant_station(location: Any, *, radius_m: float | None = None) -> str | None:
    """Return the rail Bahnhof a construction ``location`` is tied to.

    ``location`` is the provider's location mapping, shaped
    ``{"coordinates": {"lat": ..., "lon": ...}, ...}``. Anything without
    a usable coordinate pair is treated as not relevant (fail closed) and
    yields ``None``.
    """

    if not isinstance(location, dict):
        return None
    coordinates = location.get("coordinates")
    if not isinstance(coordinates, dict):
        return None
    radius = _resolve_radius_m() if radius_m is None else radius_m
    match = nearest_rail_station(coordinates.get("lat"), coordinates.get("lon"), radius)
    return match[0] if match else None


def is_transit_relevant(location: Any, *, radius_m: float | None = None) -> bool:
    """Return ``True`` iff the construction ``location`` sits within the
    configured radius of a rail Bahnhof (Wien station or Pendlerbahnhof)."""

    return relevant_station(location, radius_m=radius_m) is not None
