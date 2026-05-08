"""Tests for the boundary-recalculation cache in
``scripts/update_station_directory.py::_resolve_in_vienna_with_cache``.

Verifies the cross-run optimisation: the expensive polygon check
against ``LANDESGRENZEOGD.json`` runs only when the supplied
:class:`LocationInfo` coords have drifted past
``STATION_DRIFT_TOLERANCE_METERS`` since the last evaluation. Below
that threshold, the cached boolean is returned and the polygon check
is bypassed entirely.

Background: ``_set_pendler_flags`` runs once per station per cron.
Without the cache, every cron pays the polygon-check cost (~3000
ray-casts per station) regardless of whether the input coords
changed. With the cache, only newly discovered stations and stations
that genuinely relocated trigger the recomputation.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.update_station_directory import (  # noqa: E402
    LocationInfo,
    Station,
    _IN_VIENNA_CACHE_KEY,
    _resolve_in_vienna_with_cache,
)


def _make_station(extras: dict[str, object] | None = None) -> Station:
    return Station(
        bst_id="900100",
        bst_code="WHB",
        name="Wien Hauptbahnhof",
        extras=dict(extras or {}),
    )


def _location(lat: float, lon: float) -> LocationInfo:
    """Construct a LocationInfo with the test coords."""
    return LocationInfo(latitude=lat, longitude=lon, sources={"test"})


# ---------- cache miss → polygon check runs, cache populated ----------


def test_first_call_runs_polygon_check_and_populates_cache() -> None:
    station = _make_station()
    info = _location(48.185, 16.376)  # near Wien Hbf
    with patch(
        "scripts.update_station_directory._is_point_in_vienna",
        return_value=True,
    ) as mock_polygon:
        result = _resolve_in_vienna_with_cache(station, info)
    assert result is True
    assert mock_polygon.called
    assert mock_polygon.call_count == 1
    # Cache populated with [lat, lon, result]
    assert station.extras[_IN_VIENNA_CACHE_KEY] == [48.185, 16.376, True]


# ---------- cache hit (drift < 150 m) → polygon check skipped ----------


def test_drift_below_threshold_skips_polygon_check() -> None:
    """Coordinate Inertia at the boundary-check layer: cached result
    wins for tiny drifts."""
    station = _make_station(extras={
        # Cached basis — Wien Hbf coords from a previous run.
        _IN_VIENNA_CACHE_KEY: [48.185, 16.376, True],
    })
    # Same station, drifted ~5 m in latitude (well below 150 m).
    info = _location(48.18505, 16.376)
    with patch(
        "scripts.update_station_directory._is_point_in_vienna",
    ) as mock_polygon:
        result = _resolve_in_vienna_with_cache(station, info)
    assert result is True
    assert not mock_polygon.called, (
        "polygon check must NOT run when drift is below threshold"
    )
    # Cache must NOT update — pinning to the original basis prevents
    # drift accumulation across many sub-threshold runs.
    assert station.extras[_IN_VIENNA_CACHE_KEY] == [48.185, 16.376, True]


def test_cache_preserves_false_result() -> None:
    """A cached False (station outside Vienna) is reused verbatim."""
    station = _make_station(extras={
        _IN_VIENNA_CACHE_KEY: [48.50, 16.20, False],
    })
    info = _location(48.50001, 16.20001)  # ~1 m drift
    with patch(
        "scripts.update_station_directory._is_point_in_vienna",
    ) as mock_polygon:
        result = _resolve_in_vienna_with_cache(station, info)
    assert result is False
    assert not mock_polygon.called


# ---------- cache miss (drift >= 150 m) → polygon check runs ----------


def test_drift_above_threshold_recomputes() -> None:
    """A relocation past the inertia threshold forces a fresh polygon
    check and updates the cache to the new basis."""
    station = _make_station(extras={
        _IN_VIENNA_CACHE_KEY: [48.185, 16.376, True],
    })
    # 0.002° latitude shift ≈ 222 m (above the 150 m threshold)
    info = _location(48.187, 16.376)
    with patch(
        "scripts.update_station_directory._is_point_in_vienna",
        return_value=True,
    ) as mock_polygon:
        result = _resolve_in_vienna_with_cache(station, info)
    assert result is True
    assert mock_polygon.call_count == 1
    # Cache updates to the new basis after a real evaluation.
    assert station.extras[_IN_VIENNA_CACHE_KEY] == [48.187, 16.376, True]


def test_polygon_result_can_flip_on_relocation() -> None:
    """If a station relocates across the city boundary, the cached
    True flips to False and the cache reflects the new ground truth."""
    station = _make_station(extras={
        _IN_VIENNA_CACHE_KEY: [48.185, 16.376, True],  # was inside
    })
    # Drift > 150 m AND polygon now says outside.
    info = _location(48.30, 16.376)
    with patch(
        "scripts.update_station_directory._is_point_in_vienna",
        return_value=False,
    ):
        result = _resolve_in_vienna_with_cache(station, info)
    assert result is False
    assert station.extras[_IN_VIENNA_CACHE_KEY] == [48.30, 16.376, False]


# ---------- malformed cache → polygon check runs ----------


def test_malformed_cache_recomputes() -> None:
    """Schema-drift defence: any malformed cache entry forces a fresh
    polygon check rather than returning a wrong answer."""
    info = _location(48.185, 16.376)

    bad_caches: list[object] = [
        # Wrong type
        "stringy",
        42,
        {"latitude": 48.185, "longitude": 16.376, "result": True},
        # Wrong length
        [48.185, 16.376],
        [48.185, 16.376, True, "extra"],
        # Wrong inner types
        ["48.185", "16.376", "true"],  # all strings
        [48.185, 16.376, 1],  # int instead of bool
        [None, None, True],  # None coords
    ]

    for bad in bad_caches:
        station = _make_station(extras={_IN_VIENNA_CACHE_KEY: bad})
        with patch(
            "scripts.update_station_directory._is_point_in_vienna",
            return_value=True,
        ) as mock_polygon:
            result = _resolve_in_vienna_with_cache(station, info)
        assert mock_polygon.call_count == 1, (
            f"malformed cache {bad!r} must trigger a polygon recompute"
        )
        # And the cache must have been overwritten with a valid
        # ``[lat, lon, bool]`` triple after the recompute.
        assert station.extras[_IN_VIENNA_CACHE_KEY] == [48.185, 16.376, True]
        # Verify the cleaned-up cache is now usable for the next call.
        assert result is True
