"""Coordinate consensus for the Austrian-source station directory.

Implements the ``WL -> HAFAS -> OSM -> Google`` priority chain used by the
weekly station-directory refresh (``scripts/update_wl_stations.py``).

Wiener Linien is the authoritative source for a Vienna stop coordinate;
HAFAS (ÖBB) is the second Austrian source. When both are present and
agree (distance ``<=`` tolerance) the WL coordinate is kept. When they
disagree, OpenStreetMap arbitrates by endorsing whichever of the two
lies closer to it. OSM is consulted only as a tie-breaker — never as a
primary source — and Google Places stays the gap-fill of last resort for
a station that has *no* coordinate at all (handled elsewhere in the
pipeline), never an arbiter between two Austrian sources.

The module is deliberately pure (no I/O, no network): callers inject the
already-resolved WL / HAFAS / OSM coordinates so the policy is trivially
unit-testable and deterministic across cron runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ..utils.geo import calculate_distance_meters

#: ``WL`` and ``HAFAS`` are treated as the same location when no further
#: apart than this. Matches ``STATION_DRIFT_TOLERANCE_METERS`` (150 m): the
#: multimodal hubs in the WL∩HAFAS overlap legitimately span >100 m between
#: the tram/bus stop centroid and the rail station point, so a tighter
#: bound would flag every large station as a conflict.
DEFAULT_AGREE_TOLERANCE_M: Final = 150.0

#: OSM may only break a WL/HAFAS tie when it sits within this radius of at
#: least one candidate. Beyond it the OSM hit is implausible (wrong match /
#: stale node) and the conflict is left unresolved rather than trusting a
#: far-away arbiter.
DEFAULT_OSM_SANITY_RADIUS_M: Final = 500.0

Coordinate = tuple[float, float]


@dataclass(frozen=True)
class CoordinateDecision:
    """Outcome of :func:`resolve_at_coordinate`.

    ``decision`` is a stable machine token for logging / tests:
    ``wl_hafas_agree``, ``osm_picked_wl``, ``osm_picked_hafas``,
    ``unresolved_kept_wl`` or ``wl_only``. ``sources`` lists the provider
    tokens that contributed to the decision so the caller can merge them
    into the entry's ``source`` field.
    """

    latitude: float
    longitude: float
    chosen_source: str
    decision: str
    sources: tuple[str, ...]


def _valid(coord: Coordinate | None) -> Coordinate | None:
    """Return ``coord`` as floats iff it is a finite, in-range WGS-84 pair.

    ``None``, ``NaN``, ``inf`` and out-of-range values all collapse to
    ``None`` so the resolver can treat an unusable source as absent rather
    than raising from the distance helper.
    """

    if coord is None:
        return None
    lat, lon = coord
    # NaN fails every comparison, so the range checks also reject NaN/inf.
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return (float(lat), float(lon))


def resolve_at_coordinate(
    *,
    wl: Coordinate,
    hafas: Coordinate | None,
    osm: Coordinate | None = None,
    agree_tolerance_m: float = DEFAULT_AGREE_TOLERANCE_M,
    osm_sanity_radius_m: float = DEFAULT_OSM_SANITY_RADIUS_M,
) -> CoordinateDecision:
    """Resolve a station coordinate per the WL→HAFAS→OSM→Google priority.

    Args:
        wl: The authoritative Wiener-Linien coordinate. Required and must
            be a valid WGS-84 pair.
        hafas: The HAFAS (ÖBB) coordinate, or ``None`` when the station is
            not cross-checkable against HAFAS.
        osm: The OpenStreetMap coordinate used to arbitrate a WL/HAFAS
            disagreement, or ``None`` when unavailable.
        agree_tolerance_m: WL/HAFAS agreement threshold in metres.
        osm_sanity_radius_m: Maximum distance OSM may sit from a candidate
            and still be trusted as the arbiter.

    Returns:
        A :class:`CoordinateDecision` naming the winning coordinate, source
        and a machine-readable ``decision`` token.

    Raises:
        ValueError: If ``wl`` is not a valid WGS-84 coordinate.
    """

    wl_valid = _valid(wl)
    if wl_valid is None:
        raise ValueError("resolve_at_coordinate requires a valid WL coordinate")
    wl_lat, wl_lon = wl_valid

    hafas_valid = _valid(hafas)
    if hafas_valid is None:
        # No second Austrian source to cross-check against — WL stands.
        return CoordinateDecision(wl_lat, wl_lon, "wl", "wl_only", ("wl",))
    haf_lat, haf_lon = hafas_valid

    if calculate_distance_meters(wl_lat, wl_lon, haf_lat, haf_lon) <= agree_tolerance_m:
        return CoordinateDecision(
            wl_lat, wl_lon, "wl", "wl_hafas_agree", ("wl", "hafas")
        )

    # WL and HAFAS disagree → let OSM decide which one is correct.
    osm_valid = _valid(osm)
    if osm_valid is not None:
        osm_lat, osm_lon = osm_valid
        dist_wl = calculate_distance_meters(osm_lat, osm_lon, wl_lat, wl_lon)
        dist_haf = calculate_distance_meters(osm_lat, osm_lon, haf_lat, haf_lon)
        if min(dist_wl, dist_haf) <= osm_sanity_radius_m:
            if dist_haf < dist_wl:
                return CoordinateDecision(
                    haf_lat, haf_lon, "hafas", "osm_picked_hafas",
                    ("wl", "hafas", "osm"),
                )
            return CoordinateDecision(
                wl_lat, wl_lon, "wl", "osm_picked_wl", ("wl", "hafas", "osm")
            )

    # OSM missing or implausibly far from both → keep the highest-priority
    # source (WL) and let the caller flag the unresolved conflict.
    return CoordinateDecision(
        wl_lat, wl_lon, "wl", "unresolved_kept_wl", ("wl", "hafas")
    )
