"""Sentinel PoC: OSM ``filter_complete_places`` finite/WGS84-range
floor drift — third ingest tier still skips the canonical floor that
HAFAS and Google Places already enforce.

Threat model
------------

The 2026-05-14 "Coordinate Ingest Drift" round (PR #1485, commit
``31570fe``) closed the finite/WGS84-range floor at the HAFAS and
Google Places parser boundaries (``_extract_first_location`` /
``_parse_place``) plus the writer-level ``allow_nan=False`` pin in
:func:`src.places.merge.write_stations`. The journal entry explicitly
named the OSM sibling :func:`src.places.osm_client.filter_complete_places`
as having only an "incidental" defence: ``BoundingBox.contains`` in the
upstream :func:`_iter_stations` rejects ``±Inf`` because every finite-
range comparison with ``±inf`` returns ``False``, AND
:func:`math.isnan` rejects ``NaN`` at the filter boundary itself.

The structural drift left in the OSM filter:

  * No ``math.isfinite(lat)`` / ``math.isfinite(lon)`` check — the
    function relies on ``math.isnan`` only, so ``±Inf`` survives
    the per-place gate.
  * No WGS84 range check (``-90 ≤ lat ≤ 90``, ``-180 ≤ lon ≤ 180``)
    — the function accepts e.g. ``lat=999.0`` if the caller passes
    such a :class:`Place` directly.

The function is exported in :data:`src.places.osm_client.__all__`
and the docstring claims "non-empty name and finite coordinates" —
but the code does NOT enforce the "finite" half. Every other
ingest tier (HAFAS via ``_is_valid_wgs84_coord`` mirror, Google
Places via inline check) now applies the same canonical floor at
the parser boundary; OSM is the structural drift.

Defence-in-depth threat shape: the upstream ``BoundingBox.contains``
gate is the ONLY thing protecting against ``±Inf`` today. A future
refactor that:

  * relaxes the bounding box (e.g. switches to the European
    multi-country envelope so the project can ingest Praha / Budapest
    / Roma fallbacks) — ``±Inf`` may still slip past the new bounds
    depending on the values picked;
  * removes ``_iter_stations`` from the call chain (e.g. a new caller
    feeds :class:`Place` objects directly into ``filter_complete_places``
    from a different source);
  * a third-party caller (the function is a public API surface)
    invokes ``filter_complete_places`` on places of unknown
    provenance;

bypasses the indirect protection and lands non-finite or out-of-range
coordinates into the canonical merge / writer chain. The writer-level
``allow_nan=False`` pin catches ``NaN``/``Inf`` at the JSON sink, but
silently corrupts the artefact on a fourth tier added without the
floor.

Sinks (public artefacts)
------------------------

  * ``data/stations.json`` — committed by the weekly
    ``update-stations.yml`` cron job. Served via GitHub Pages
    (``https://origamihase.github.io/wien-oepnv/stations.json``)
    and the raw.githubusercontent.com mirror.
  * Operator-facing logs (``log/diagnostics.log``) — every merge
    diagnostic that emits ``place.latitude`` / ``place.longitude``.

Severity: LOW-MEDIUM — structural inconsistency rather than an
exploited bypass today (the ``BoundingBox.contains`` gate
incidentally protects against ``±Inf``). Closes the third-tier
drift so the canonical-floor invariant is uniform across all three
ingest tiers (OSM, HAFAS, Google Places).

The fix
-------

Extend :func:`src.places.osm_client.filter_complete_places` to
mirror the HAFAS / Google Places shape:

  * ``math.isfinite(lat) and math.isfinite(lon)`` — catches
    ``NaN`` (replaces the existing isnan check), ``+Inf``, ``-Inf``.
  * ``-90.0 ≤ lat ≤ 90.0 and -180.0 ≤ lon ≤ 180.0`` — catches
    out-of-range values (defence against a future bounding-box
    relaxation or direct caller).

The replacement is additive against the existing ``isnan`` defence
(``isfinite`` is the strict superset of ``not isnan``). The
WGS84 range check is the same shape as :func:`src.utils.geo._is_valid_coord`
and :func:`src.places.hafas_client._is_valid_wgs84_coord`.
"""

from __future__ import annotations

import inspect
import math

import pytest

from src.places import osm_client as osm_module
from src.places.client import Place
from src.places.osm_client import filter_complete_places


# Sentinel marker shared by every PoC site.
SENTINEL_OSM_FILTER_FINITE_RANGE_DRIFT = (
    "OSM filter_complete_places canonical-floor finite/WGS84-range drift"
)


def _make_place(latitude: float, longitude: float) -> Place:
    """Construct a Place with attacker-controlled coordinates.

    The :class:`Place` dataclass accepts any float values without
    additional validation (the ingest-tier parsers are responsible
    for the canonical floor; the dataclass is a transport vehicle
    only). This helper exposes that gap so the PoC can plant
    non-finite or out-of-range values directly.
    """
    return Place(
        place_id="osm:node/12345",
        name="Wien Hauptbahnhof",
        latitude=latitude,
        longitude=longitude,
        types=["train_station"],
        formatted_address=None,
    )


# ---------------------------------------------------------------------------
# (1) Pre-fix proof: ``filter_complete_places`` accepts ±Inf and
#     out-of-WGS84-range coordinates because it only checks NaN.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lat", "lon", "label"),
    [
        # Non-finite values: ``math.isnan`` catches ``NaN`` only —
        # ``±Inf`` slips through pre-fix.
        (float("inf"), 16.37, "+Inf latitude"),
        (-float("inf"), 16.37, "-Inf latitude"),
        (48.21, float("inf"), "+Inf longitude"),
        (48.21, -float("inf"), "-Inf longitude"),
        (float("inf"), float("inf"), "both ±Inf"),
        # Out-of-WGS84-range values: the function does NOT check
        # the geodetic valid bounds at all pre-fix.
        (90.001, 16.37, "lat just above 90"),
        (-90.001, 16.37, "lat just below -90"),
        (48.21, 180.001, "lon just above 180"),
        (48.21, -180.001, "lon just below -180"),
        (999.0, 999.0, "both wildly out of range"),
        (1e10, 1e10, "integer-overflow scale (10**10)"),
        (-1e10, -1e10, "negative integer-overflow scale"),
    ],
)
def test_osm_filter_rejects_non_finite_or_out_of_wgs84_range(
    lat: float, lon: float, label: str,
) -> None:
    """A :class:`Place` with non-finite or out-of-WGS84-range
    coordinates MUST NOT survive ``filter_complete_places``.

    Pre-fix the function only checks :func:`math.isnan`, so all
    twelve cases above flow through verbatim. Post-fix the
    canonical-floor finite + WGS84-range check rejects each of them
    at the filter boundary, mirroring the HAFAS
    (``_is_valid_wgs84_coord``) and Google Places (inline check)
    parser-boundary defences pinned by the prior round.
    """
    place = _make_place(lat, lon)
    result = filter_complete_places([place])
    assert result == [], (
        f"{label}: lat={lat!r}, lon={lon!r} survived "
        f"filter_complete_places pre-fix. The canonical-floor "
        f"finite + WGS84-range check at the OSM filter boundary "
        f"MUST drop this place."
    )


# ---------------------------------------------------------------------------
# (2) NaN regression: the existing isnan defence MUST stay intact.
#     The fix is additive only — replacing ``isnan`` with ``isfinite``
#     extends the rejection set without removing anything that was
#     previously rejected.
# ---------------------------------------------------------------------------


def test_osm_filter_continues_to_reject_nan_after_fix() -> None:
    """The existing ``math.isnan`` defence must stay intact post-fix.

    ``math.isfinite`` is a strict superset of ``not math.isnan``, so
    extending the check from ``isnan`` to ``not isfinite`` is
    additive only — every value previously rejected is still
    rejected.
    """
    nan_place_lat = _make_place(float("nan"), 16.37)
    nan_place_lon = _make_place(48.21, float("nan"))
    nan_place_both = _make_place(float("nan"), float("nan"))

    assert filter_complete_places([nan_place_lat]) == []
    assert filter_complete_places([nan_place_lon]) == []
    assert filter_complete_places([nan_place_both]) == []


# ---------------------------------------------------------------------------
# (3) Legitimate-content invariant: every plausible Vienna / European
#     coordinate survives the post-fix scrub. The fix must not eat
#     valid geodetic data.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lat", "lon", "label"),
    [
        (48.2082, 16.3738, "Wien Stephansplatz"),
        (48.1851, 16.3760, "Wien Hauptbahnhof"),
        (48.2425, 16.3895, "Wien Floridsdorf"),
        # Boundary values — the WGS84 range is inclusive on both ends.
        (90.0, 180.0, "extreme north-east corner"),
        (-90.0, -180.0, "extreme south-west corner"),
        (0.0, 0.0, "Null Island"),
        # Distant-terminus stations the ÖBB directory legitimately stores.
        (52.5251, 13.3690, "Berlin Hbf"),
        (41.9009, 12.5025, "Roma Termini"),
        (47.4979, 19.0402, "Budapest Keleti"),
    ],
)
def test_osm_filter_preserves_legitimate_coordinates(
    lat: float, lon: float, label: str,
) -> None:
    """Legitimate WGS84 coordinates (including the inclusive
    boundary values ±90 / ±180) survive the canonical-floor
    finite + range check unchanged.

    The fix is additive only against the non-finite / out-of-
    range family — every legitimate coordinate from the prior
    canonical inventory still passes through.
    """
    place = _make_place(lat, lon)
    result = filter_complete_places([place])
    assert result == [place], (
        f"{label}: lat={lat}, lon={lon} was unexpectedly rejected "
        f"by filter_complete_places. The fix is additive only; "
        f"legitimate WGS84 coordinates MUST survive."
    )


# ---------------------------------------------------------------------------
# (4) Inventory invariant: the OSM filter source MUST mirror the
#     canonical-floor shape used by the HAFAS / Google Places parsers.
# ---------------------------------------------------------------------------


def test_inventory_osm_filter_uses_canonical_floor_check() -> None:
    """The OSM ``filter_complete_places`` source MUST reference the
    canonical finite + WGS84-range check shape — same shape pinned
    in HAFAS (``_is_valid_wgs84_coord``) and Google Places
    (inline check). A future refactor that drops the check (or
    regresses to the ``isnan``-only defence) fails this test on
    the source-grep level.
    """
    source = inspect.getsource(filter_complete_places)
    # Post-fix the function MUST use ``math.isfinite`` (strict
    # superset of ``not math.isnan``) to catch ``±Inf`` in addition
    # to NaN.
    assert "isfinite" in source, (
        "filter_complete_places must use math.isfinite (which catches "
        "NaN AND ±Inf) instead of math.isnan (which catches NaN only). "
        "The canonical-floor invariant pinned by the 2026-05-14 round "
        "requires the strict-superset check at every ingest tier."
    )
    # Post-fix the function MUST also enforce the WGS84 valid range.
    # Both bounds (90, 180) MUST appear so a future refactor that
    # accidentally drops one bound fails this test.
    assert "90" in source and "180" in source, (
        "filter_complete_places must enforce the WGS84 valid range "
        "(-90 ≤ lat ≤ 90, -180 ≤ lon ≤ 180) at the filter boundary "
        "to mirror the HAFAS / Google Places defences."
    )


# ---------------------------------------------------------------------------
# (5) Defence-in-depth invariant: at runtime the post-fix filter
#     produces a result equivalent to applying ``_is_valid_wgs84_coord``-
#     shape predicates from the sibling tier. The structural test below
#     pins the same call-graph as ``test_inventory_every_places_parser_validates_finite_range``
#     in ``test_sentinel_coordinate_nan_inf_range_drift.py``.
# ---------------------------------------------------------------------------


def test_inventory_osm_filter_matches_sibling_canonical_floor_runtime() -> None:
    """For each value in the canonical inventory of non-finite /
    out-of-range coordinate values, the OSM filter MUST drop it —
    same semantics as the HAFAS / Google Places parser defences.

    The test mirrors the sibling-parser inventory test pinned by the
    prior round (``test_sentinel_coordinate_nan_inf_range_drift.py``)
    so the three ingest tiers have a single source of truth for the
    canonical-floor invariant.
    """
    canonical_inventory: tuple[tuple[float, float, str], ...] = (
        (float("nan"), 16.37, "NaN lat"),
        (48.21, float("nan"), "NaN lon"),
        (float("inf"), 16.37, "+Inf lat"),
        (48.21, float("inf"), "+Inf lon"),
        (-float("inf"), 16.37, "-Inf lat"),
        (48.21, -float("inf"), "-Inf lon"),
        (90.001, 16.37, "lat just above max"),
        (-90.001, 16.37, "lat just below min"),
        (48.21, 180.001, "lon just above max"),
        (48.21, -180.001, "lon just below min"),
    )
    for lat, lon, label in canonical_inventory:
        place = _make_place(lat, lon)
        result = filter_complete_places([place])
        assert result == [], (
            f"{label}: lat={lat!r}, lon={lon!r} survived OSM filter — "
            f"the canonical-floor invariant requires the same rejection "
            f"semantics as the HAFAS and Google Places parsers."
        )


# ---------------------------------------------------------------------------
# (6) Module-level smoke test: confirm the module imports cleanly so
#     the structural inventory tests above don't false-pass on an
#     ImportError-aborted module.
# ---------------------------------------------------------------------------


def test_module_imports_cleanly() -> None:
    """Smoke test — confirm the OSM module imports cleanly."""
    assert osm_module.filter_complete_places is filter_complete_places
    assert callable(filter_complete_places)
    assert math.isfinite(0.0)  # sanity: stdlib math works
