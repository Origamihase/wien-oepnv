"""Tests for ``src/utils/geo.py``.

Covers:

* ``calculate_distance_meters`` — Haversine basics, edge cases, and
  input validation.
* ``apply_coordinate_inertia`` — the four resolution rules (no-new,
  no-existing, drift-below-threshold, drift-above-threshold) plus the
  invalid-coord fallback.
"""
from __future__ import annotations

import math

import pytest

from src.utils.geo import (
    STATION_DRIFT_TOLERANCE_METERS,
    apply_coordinate_inertia,
    calculate_distance_meters,
)


# ---------- calculate_distance_meters ----------


def test_distance_zero_for_same_point() -> None:
    assert calculate_distance_meters(48.2, 16.4, 48.2, 16.4) == pytest.approx(0.0)


def test_distance_one_degree_latitude_at_equator() -> None:
    """1° of latitude is ~111 km everywhere on Earth."""
    distance = calculate_distance_meters(0.0, 0.0, 1.0, 0.0)
    assert 110_000 < distance < 112_000


def test_distance_known_route_wien_hbf_to_flughafen() -> None:
    """Wien Hbf (48.185, 16.376) to Flughafen Wien (48.120, 16.564) is
    ~16 km along the great circle. Use a wide tolerance — the Haversine
    on an oblate Earth has ~0.5% systematic bias."""
    distance = calculate_distance_meters(48.185, 16.376, 48.120, 16.564)
    assert 14_000 < distance < 18_000


def test_distance_symmetric() -> None:
    forward = calculate_distance_meters(48.0, 16.0, 49.0, 17.0)
    backward = calculate_distance_meters(49.0, 17.0, 48.0, 16.0)
    assert forward == pytest.approx(backward)


def test_distance_rejects_nan() -> None:
    with pytest.raises(ValueError, match="finite"):
        calculate_distance_meters(float("nan"), 16.0, 48.0, 17.0)


def test_distance_rejects_inf() -> None:
    with pytest.raises(ValueError, match="finite"):
        calculate_distance_meters(48.0, 16.0, float("inf"), 17.0)


def test_distance_rejects_out_of_range_lat() -> None:
    with pytest.raises(ValueError, match="Latitude"):
        calculate_distance_meters(91.0, 16.0, 48.0, 17.0)


def test_distance_rejects_out_of_range_lon() -> None:
    with pytest.raises(ValueError, match="Longitude"):
        calculate_distance_meters(48.0, 181.0, 48.0, 17.0)


def test_distance_extreme_corners() -> None:
    """North pole to South pole — half the earth's circumference."""
    distance = calculate_distance_meters(-90.0, 0.0, 90.0, 0.0)
    expected = math.pi * 6_371_000.0  # half-circumference
    assert distance == pytest.approx(expected, rel=0.01)


# ---------- apply_coordinate_inertia ----------


def test_inertia_no_new_keeps_existing() -> None:
    """Rule 1: no new coords → keep existing."""
    result = apply_coordinate_inertia(48.0, 16.0, None, None)
    assert result == (48.0, 16.0)


def test_inertia_partial_new_keeps_existing() -> None:
    """Rule 1 (partial): only one of the new coords is present →
    treat as no usable update."""
    assert apply_coordinate_inertia(48.0, 16.0, 49.0, None) == (48.0, 16.0)
    assert apply_coordinate_inertia(48.0, 16.0, None, 17.0) == (48.0, 16.0)


def test_inertia_no_existing_accepts_new() -> None:
    """Rule 2: first-time coords always taken as authoritative."""
    assert apply_coordinate_inertia(None, None, 48.0, 16.0) == (48.0, 16.0)


def test_inertia_partial_existing_accepts_new() -> None:
    """Rule 2 (partial): half-existing coords are unusable; accept new."""
    assert apply_coordinate_inertia(48.0, None, 49.0, 17.0) == (49.0, 17.0)
    assert apply_coordinate_inertia(None, 16.0, 49.0, 17.0) == (49.0, 17.0)


def test_inertia_below_threshold_keeps_existing() -> None:
    """Rule 3a: drift below tolerance → keep existing (absorbed)."""
    # Vienna airport: shift latitude by ~5 m (<< 150 m tolerance)
    existing = (48.12056, 16.563659)
    new_lat = 48.12056 + 5e-5  # ~5.5 m north
    result = apply_coordinate_inertia(*existing, new_lat, 16.563659)
    assert result == existing  # absorbed


def test_inertia_at_threshold_accepts_new() -> None:
    """Rule 3b: drift at/above tolerance → accept new (relocation)."""
    # Same airport but shift latitude by ~200 m (>> 150 m tolerance).
    # 0.002 deg lat ≈ 222 m at this latitude.
    existing = (48.12056, 16.563659)
    new = (48.12056 + 0.002, 16.563659)
    result = apply_coordinate_inertia(*existing, *new)
    assert result == new


def test_inertia_custom_tolerance() -> None:
    """Threshold is overridable per call."""
    existing = (48.0, 16.0)
    # 0.001° lat at 48°N is ~111 m — between the default 150 m
    # and a tight 50 m threshold.
    new = (48.001, 16.0)
    # Default: 111 m < 150 m → absorbed
    assert apply_coordinate_inertia(*existing, *new) == existing
    # Tight: 111 m > 50 m → accepted
    assert apply_coordinate_inertia(
        *existing, *new, tolerance_m=50.0
    ) == new


def test_inertia_rule4_invalid_existing_accepts_new() -> None:
    """Rule 4: invalid existing coords (out-of-range) are treated as
    'no comparable existing' — accept new."""
    # Existing lat=999 is out of range → ValueError → accept new
    assert apply_coordinate_inertia(999.0, 16.0, 49.0, 17.0) == (49.0, 17.0)


def test_inertia_default_tolerance_constant() -> None:
    """The exported constant matches the documented value."""
    assert STATION_DRIFT_TOLERANCE_METERS == 150.0
