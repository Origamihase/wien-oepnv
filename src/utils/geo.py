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
    "use_cached_polygon_result",
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


def _is_valid_coord(lat: float | None, lon: float | None) -> bool:
    """Return ``True`` iff both inputs are finite numbers within valid
    lat/lon ranges (-90..90 / -180..180). ``None``, ``NaN``, ``inf``,
    and out-of-range values all return ``False``.

    This is the same validation :func:`calculate_distance_meters`
    performs on its inputs, exposed as a predicate so callers can
    pre-filter pairs without having to invoke (and catch
    ``ValueError`` from) the distance computation.
    """
    if lat is None or lon is None:
        return False
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return False
    if not -90.0 <= lat <= 90.0:
        return False
    if not -180.0 <= lon <= 180.0:
        return False
    return True


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

    1. **No usable new coords** (either component is ``None``,
       ``NaN``, ``inf``, or out of range) → keep existing. The
       upstream payload didn't supply trustworthy coords for this
       station, so there's nothing to merge — and crucially we
       refuse to propagate invalid values into the cache.
    2. **No usable existing coords** (any of the same conditions) →
       accept new. First-time coords or recovery from a corrupt
       cached value: trust the upstream now.
    3. **Both pairs valid** → compute Haversine distance.
       * Below ``tolerance_m`` → keep existing (absorbed drift).
       * At or above ``tolerance_m`` → accept new (genuine
         relocation).

    Note the asymmetry between rules 1 and 2: invalid NEW always
    falls through to "keep existing" (defence against poisoned
    upstream payload), while invalid EXISTING falls through to
    "accept new" (recovery from corrupt local cache). Hardened
    against ``NaN`` and out-of-range values that an earlier draft
    would have propagated under rule 4.

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
        (when drift was absorbed or new is invalid) or the new pair
        (when the new coordinates are authoritative). Either
        component is ``None`` only when both input pairs were
        unusable.
    """
    # Rule 1: invalid new coords → keep existing (refuse to propagate
    # NaN / out-of-range / None into the cache).
    if not _is_valid_coord(new_lat, new_lon):
        return existing_lat, existing_lon

    # Rule 2: invalid existing coords → accept (validated) new.
    if not _is_valid_coord(existing_lat, existing_lon):
        return new_lat, new_lon

    # Rule 3: both valid → distance-based decision. Both pairs already
    # passed range/finite validation, so ``calculate_distance_meters``
    # cannot raise — the inner ``try`` is purely defensive against
    # future validation changes.
    try:
        drift = calculate_distance_meters(
            existing_lat, existing_lon, new_lat, new_lon  # type: ignore[arg-type]
        )
    except ValueError:  # pragma: no cover - validated above
        return new_lat, new_lon

    if drift < tolerance_m:
        return existing_lat, existing_lon
    return new_lat, new_lon


def use_cached_polygon_result(
    cached_lat: float | None,
    cached_lon: float | None,
    cached_result: bool | None,
    new_lat: float,
    new_lon: float,
    tolerance_m: float = STATION_DRIFT_TOLERANCE_METERS,
) -> bool | None:
    """Return ``cached_result`` iff the cache is reusable for ``new_lat`` /
    ``new_lon``; otherwise return ``None`` to signal "recompute required".

    "Reusable" means:

    1. The cache triple has the right shape — ``cached_result`` is a
       bool and the cached coords pass :func:`_is_valid_coord`.
    2. The cached coords are within ``tolerance_m`` of the new coords
       (Haversine distance below threshold).

    Used by callers that need to skip an expensive boolean lookup
    (e.g. point-in-polygon against a city boundary) when the input
    coords haven't drifted far enough to plausibly change the result.
    Returning ``None`` rather than a bool keeps the "no cache" /
    "result is False" cases unambiguous at the call site.

    Args:
        cached_lat: Latitude from the previous evaluation. ``None``,
            ``NaN``, or out of range → cache miss.
        cached_lon: Longitude from the previous evaluation. Same
            invalidation rules as ``cached_lat``.
        cached_result: Boolean result from the previous evaluation.
            Anything other than a real ``bool`` (including subclasses
            and integers like ``0``/``1``) → cache miss.
        new_lat: Latitude to look up now.
        new_lon: Longitude to look up now.
        tolerance_m: Maximum drift in metres for the cache to be
            considered fresh. Defaults to
            :data:`STATION_DRIFT_TOLERANCE_METERS`.

    Returns:
        ``cached_result`` (a bool) if the cache is reusable, else
        ``None``. Callers that get ``None`` must perform the real
        computation and update the cache.
    """
    if not isinstance(cached_result, bool):
        return None
    if not _is_valid_coord(cached_lat, cached_lon):
        return None
    if not _is_valid_coord(new_lat, new_lon):
        return None
    try:
        drift = calculate_distance_meters(
            cached_lat, cached_lon, new_lat, new_lon  # type: ignore[arg-type]
        )
    except ValueError:  # pragma: no cover - validated above
        return None
    if drift < tolerance_m:
        return cached_result
    return None
