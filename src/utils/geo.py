"""Geographic utilities — distance computation and coordinate inertia.

This module is the project's single source of truth for geo-spatial
math. It exposes:

* :func:`calculate_distance_meters` — great-circle distance between two
  WGS-84 GPS coordinates, computed with the Haversine formula. The
  implementation is dependency-free (stdlib ``math`` only) and rejects
  non-finite or out-of-range inputs early so a malformed upstream payload
  cannot poison downstream callers.
* :data:`STATION_DRIFT_TOLERANCE_METERS` — the coordinate-inertia
  threshold used by ``scripts/update_station_directory.py`` to decide
  whether an upstream API's new coordinate replaces the existing one.
* :func:`apply_coordinate_inertia` — the inertia helper that returns
  either the existing coords (drift below threshold, absorb the noise)
  or the new coords (genuine relocation beyond threshold).

Why coordinate inertia?

Large transit stations (airports, multi-platform mainline stations)
have a "valid" coordinate that legitimately varies by 10–50 m
depending on the data provider's chosen reference point (entrance,
platform centre, ticketing kiosk). Each upstream refresh nudges the
recorded coordinate by a few metres; without dampening, every refresh
cycle pollutes ``data/stations.json`` with churn-only diffs and
breaks brittle ``pytest.approx`` test assertions on the new values.

The 150 m threshold is well above typical inter-provider drift
(most stations stay within ~30 m even after years of provider
re-surveys) and well below any real station relocation (a station
that moves 150+ m has effectively been rebuilt). Tunes naturally
between "absorb noise" and "track legitimate moves".

The duplicate Haversine implementation in ``src/places/normalize.py``
is now a thin re-export of :func:`calculate_distance_meters` so the
formula lives in exactly one place.
"""
from __future__ import annotations

import math
from typing import Final

__all__ = [
    "STATION_DRIFT_TOLERANCE_METERS",
    "apply_coordinate_inertia",
    "calculate_distance_meters",
]

_EARTH_RADIUS_M: Final = 6_371_000.0

#: Maximum coordinate drift (in metres) that the inertia helper will
#: absorb before accepting an upstream re-survey. See module docstring
#: for tuning rationale.
STATION_DRIFT_TOLERANCE_METERS: Final = 150.0


def calculate_distance_meters(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """Return the great-circle distance between two WGS-84 coordinates.

    Computes the Haversine distance — the shortest path along the
    surface of a sphere of radius :data:`_EARTH_RADIUS_M`. The formula
    is accurate to ~0.5% over typical inter-station distances; that
    error is well below the inertia threshold and irrelevant for any
    use within the project.

    Args:
        lat1: Latitude of the first point in decimal degrees.
        lon1: Longitude of the first point in decimal degrees.
        lat2: Latitude of the second point in decimal degrees.
        lon2: Longitude of the second point in decimal degrees.

    Returns:
        Distance between the two points in metres (float, ≥ 0).

    Raises:
        ValueError: If any input is non-finite (NaN/inf) or outside
            the valid lat/lon ranges (``-90 ≤ lat ≤ 90``,
            ``-180 ≤ lon ≤ 180``).
    """
    if not (
        math.isfinite(lat1)
        and math.isfinite(lon1)
        and math.isfinite(lat2)
        and math.isfinite(lon2)
    ):
        raise ValueError("Coordinates must be finite numbers")
    if not (-90.0 <= lat1 <= 90.0 and -90.0 <= lat2 <= 90.0):
        raise ValueError("Latitude must be between -90.0 and 90.0")
    if not (-180.0 <= lon1 <= 180.0 and -180.0 <= lon2 <= 180.0):
        raise ValueError("Longitude must be between -180.0 and 180.0")

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    sin_half_d_phi = math.sin(d_phi / 2.0)
    sin_half_d_lambda = math.sin(d_lambda / 2.0)

    a = (
        sin_half_d_phi**2
        + math.cos(phi1) * math.cos(phi2) * sin_half_d_lambda**2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
    return _EARTH_RADIUS_M * c


def apply_coordinate_inertia(
    existing_lat: float | None,
    existing_lon: float | None,
    new_lat: float | None,
    new_lon: float | None,
    tolerance_m: float = STATION_DRIFT_TOLERANCE_METERS,
) -> tuple[float | None, float | None]:
    """Return the coordinate pair that should replace the existing one.

    Implements "coordinate inertia": absorb upstream-API coordinate
    drift below ``tolerance_m`` so ``data/stations.json`` does not
    churn on every refresh cycle. The resolution rules are:

    1. **No new coords** (either component is ``None``) → keep
       existing. The upstream payload didn't supply usable coords for
       this station, so there's nothing to merge.
    2. **No existing coords** → accept new. First-time coords for a
       station are always taken as authoritative.
    3. **Both present** → compute Haversine distance.
       * Below ``tolerance_m`` → keep existing (absorbed drift).
       * At or above ``tolerance_m`` → accept new (genuine relocation).
    4. **Invalid coords** (out of range / non-finite) → accept new
       (ValueError from the distance helper is treated as "no
       comparable existing coord", same as rule 2). This trusts the
       upstream over a corrupt local cache.

    Args:
        existing_lat: Existing latitude or ``None``.
        existing_lon: Existing longitude or ``None``.
        new_lat: Newly fetched latitude or ``None``.
        new_lon: Newly fetched longitude or ``None``.
        tolerance_m: Inertia threshold in metres. Defaults to
            :data:`STATION_DRIFT_TOLERANCE_METERS`. Override in tests
            or to tighten/loosen for specific station classes.

    Returns:
        ``(latitude, longitude)`` tuple — either the existing pair
        (when drift was absorbed) or the new pair (when the new
        coordinates are authoritative). Either component can be
        ``None`` only when both input pairs were ``None``.
    """
    # Rule 1: no new coords → keep existing.
    if new_lat is None or new_lon is None:
        return existing_lat, existing_lon

    # Rule 2: no existing coords → accept new.
    if existing_lat is None or existing_lon is None:
        return new_lat, new_lon

    # Rules 3 & 4: compare distance, accept new on invalid input.
    try:
        drift = calculate_distance_meters(
            existing_lat, existing_lon, new_lat, new_lon
        )
    except ValueError:
        return new_lat, new_lon

    if drift < tolerance_m:
        return existing_lat, existing_lon
    return new_lat, new_lon
