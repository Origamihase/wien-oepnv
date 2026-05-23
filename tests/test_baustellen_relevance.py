"""Tests for the Baustellen ÖPNV-relevance filter.

The provider must only surface construction sites at/near a rail Bahnhof
(Wien station or Pendlerbahnhof); ordinary road works anywhere else in
the city must be dropped so the feed stays a focused transit signal.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import update_baustellen_cache
from src.build_feed import _post_filter_baustellen
from src.providers import baustellen
from src.providers.baustellen import (
    DEFAULT_STATION_RADIUS_M,
    is_transit_relevant,
    relevant_station,
)
from src.utils import stations

# Real directory coordinates (data/stations.json).
WIEN_HBF = (48.185184, 16.376413)  # bst_id 900100, in_vienna
MOEDLING = (48.085628, 16.295474)  # bst_id 1377, pendler
# A point deep in the Donau-Auen floodplain — no rail Bahnhof for km.
FAR_AWAY = (48.170000, 16.520000)

SAMPLE_PATH = Path(__file__).resolve().parents[1] / "data" / "samples" / "baustellen_sample.geojson"


def _loc(lat: float, lon: float) -> dict:
    return {"address": "Teststraße", "coordinates": {"lat": lat, "lon": lon}}


@pytest.fixture
def single_station(monkeypatch: pytest.MonkeyPatch):
    """Replace the rail-station set with one synthetic Bahnhof so distance
    assertions are independent of the real directory."""

    station = (("Test Bahnhof", 48.2000, 16.3700),)
    monkeypatch.setattr(stations, "_rail_station_coordinates", lambda: station)
    return station


# --- nearest_rail_station: geometry / radius ----------------------------------


def test_nearest_rail_station_matches_at_zero_distance(single_station) -> None:
    match = stations.nearest_rail_station(48.2000, 16.3700, 150.0)
    assert match is not None
    assert match[0] == "Test Bahnhof"
    assert match[1] == pytest.approx(0.0, abs=1.0)


def test_nearest_rail_station_within_radius(single_station) -> None:
    # ~100 m north of the station (0.0009° lat ≈ 100 m).
    assert stations.nearest_rail_station(48.2009, 16.3700, 150.0) is not None


def test_nearest_rail_station_outside_radius(single_station) -> None:
    # ~300 m north — dropped at 150 m, kept once the radius is widened.
    assert stations.nearest_rail_station(48.2027, 16.3700, 150.0) is None
    assert stations.nearest_rail_station(48.2027, 16.3700, 500.0) is not None


@pytest.mark.parametrize(
    "lat, lon",
    [
        (None, 16.37),
        (48.2, None),
        (float("nan"), 16.37),
        (48.2, float("inf")),
        (95.0, 16.37),  # latitude out of European coercion range
        ("x", 16.37),
    ],
)
def test_nearest_rail_station_fails_closed_on_bad_coords(single_station, lat, lon) -> None:
    assert stations.nearest_rail_station(lat, lon, 150.0) is None


def test_nearest_rail_station_rejects_nonpositive_radius(single_station) -> None:
    assert stations.nearest_rail_station(48.2000, 16.3700, 0.0) is None
    assert stations.nearest_rail_station(48.2000, 16.3700, -10.0) is None


# --- is_transit_relevant / relevant_station -----------------------------------


def test_relevant_station_at_real_bahnhof() -> None:
    name = relevant_station(_loc(*WIEN_HBF))
    assert name is not None
    assert "Hauptbahnhof" in name


def test_pendlerbahnhof_is_relevant() -> None:
    assert is_transit_relevant(_loc(*MOEDLING)) is True


def test_far_away_road_works_is_not_relevant() -> None:
    assert is_transit_relevant(_loc(*FAR_AWAY)) is False


@pytest.mark.parametrize(
    "location",
    [
        None,
        "nope",
        {},
        {"address": "Teststraße"},  # no coordinates
        {"coordinates": "nope"},
        {"coordinates": {"lat": None, "lon": None}},
        {"coordinates": {"lat": float("nan"), "lon": 16.37}},
    ],
)
def test_is_transit_relevant_fails_closed(location) -> None:
    assert is_transit_relevant(location) is False


def test_radius_override_widens_match(monkeypatch: pytest.MonkeyPatch, single_station) -> None:
    far = _loc(48.2027, 16.3700)  # ~300 m from the synthetic station
    assert is_transit_relevant(far) is False
    monkeypatch.setenv("BAUSTELLEN_STATION_RADIUS_M", "500")
    assert is_transit_relevant(far) is True


# --- radius resolution / clamping ---------------------------------------------


def test_resolve_radius_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BAUSTELLEN_STATION_RADIUS_M", raising=False)
    assert baustellen._resolve_radius_m() == DEFAULT_STATION_RADIUS_M


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("500", 500.0),
        ("5", 25.0),  # clamped up to the minimum
        ("999999", 2000.0),  # clamped down to the maximum
        ("abc", DEFAULT_STATION_RADIUS_M),
        ("inf", DEFAULT_STATION_RADIUS_M),
        ("  ", DEFAULT_STATION_RADIUS_M),
    ],
)
def test_resolve_radius_clamping(monkeypatch: pytest.MonkeyPatch, raw: str, expected: float) -> None:
    monkeypatch.setenv("BAUSTELLEN_STATION_RADIUS_M", raw)
    assert baustellen._resolve_radius_m() == expected


# --- _post_filter_baustellen --------------------------------------------------


def test_post_filter_keeps_relevant_drops_noise_passes_stubs() -> None:
    relevant = {"title": "Umbau", "description": "x", "location": _loc(*WIEN_HBF)}
    noise = {"title": "Hinterhof", "description": "x", "location": _loc(*FAR_AWAY)}
    no_coords = {"title": "Ohne Geo", "description": "x"}
    stub = {"guid": "meta-only"}
    sentinel = "passthrough-non-dict"

    result = _post_filter_baustellen([relevant, noise, no_coords, stub, sentinel])

    assert relevant in result
    assert noise not in result
    assert no_coords not in result  # no coordinates → fail closed
    assert stub in result  # title/description-less stub passes through
    assert sentinel in result  # non-dict passes through


# --- _first_lonlat (geometry descent) -----------------------------------------


@pytest.mark.parametrize(
    "coordinates, expected",
    [
        ([16.4, 48.2], (16.4, 48.2)),  # Point
        ([[16.4, 48.2], [16.5, 48.3]], (16.4, 48.2)),  # LineString
        ([[[16.4, 48.2], [16.5, 48.3]]], (16.4, 48.2)),  # Polygon ring
        ([[[[16.4, 48.2]]]], (16.4, 48.2)),  # MultiPolygon
    ],
)
def test_first_lonlat_descends_geometries(coordinates, expected) -> None:
    assert update_baustellen_cache._first_lonlat(coordinates) == expected


@pytest.mark.parametrize(
    "coordinates",
    [
        None,
        [],
        [16.4],  # too short
        [True, False],  # bools are not coordinates
        "16.4,48.2",
        [[[[[[[[[[16.4, 48.2]]]]]]]]]],  # deeper than _MAX_COORD_DEPTH
    ],
)
def test_first_lonlat_rejects_bad_geometries(coordinates) -> None:
    assert update_baustellen_cache._first_lonlat(coordinates) is None


# --- end-to-end on the bundled sample -----------------------------------------


def test_sample_payload_is_all_transit_relevant() -> None:
    payload = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    events = update_baustellen_cache._collect_events(payload)
    assert len(events) == 2
    assert all(is_transit_relevant(event.get("location")) for event in events)
    # The LineString feature must still yield a usable coordinate.
    assert events[1]["location"]["coordinates"]["lat"] == pytest.approx(48.085628)
