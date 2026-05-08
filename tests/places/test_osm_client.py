"""Tests for the OpenStreetMap Overpass API client.

Covers the parser (Overpass JSON → :class:`OSMStation` → :class:`Place`),
the bounding-box guard, the User-Agent contract, and the
endpoint-allow-list enforcement on env overrides. The HTTP layer is
mocked at ``request_safe`` so the suite runs with no real network IO.
"""

from __future__ import annotations

from typing import Any
from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
import requests

from src.places.merge import BoundingBox
from src.places.osm_client import (
    DEFAULT_OVERPASS_ENDPOINTS,
    OSMOverpassClient,
    OSMOverpassConfig,
    OSMOverpassError,
    OSMStation,
    VIENNA_BOUNDING_BOX,
    build_overpass_query,
    convert_to_place,
    filter_complete_places,
    fetch_osm_places,
    get_overpass_endpoint,
)
from src.places import osm_client as osm_module


# --------------------------------------------------------------------- helpers


@pytest.fixture
def reset_breaker() -> Iterator[None]:
    """Reset the module-level breaker so tests don't leak failure state."""
    osm_module._BREAKER.reset()
    try:
        yield
    finally:
        osm_module._BREAKER.reset()


def _make_response(payload: dict[str, Any]) -> MagicMock:
    response = MagicMock(spec=requests.Response)
    response.status_code = 200
    response.json.return_value = payload
    return response


def _config(endpoint: str | None = None) -> OSMOverpassConfig:
    return OSMOverpassConfig(
        endpoint=endpoint or DEFAULT_OVERPASS_ENDPOINTS[0],
        user_agent="wien-oepnv-test/1.0 (contact: test@example.com)",
    )


# ---------------------------------------------------------------- parser


def test_overpass_query_includes_all_required_tags() -> None:
    query = build_overpass_query(VIENNA_BOUNDING_BOX, query_timeout_s=25)
    for tag_pair in (
        '"public_transport"="station"',
        '"public_transport"="stop_area"',
        '"railway"="station"',
        '"railway"="halt"',
    ):
        assert tag_pair in query, f"Expected {tag_pair!r} in query"
    for kind in ("node", "way", "relation"):
        assert f"{kind}[" in query
    assert "[out:json][timeout:25]" in query
    assert "out center tags" in query


def test_overpass_query_uses_bounding_box_envelope() -> None:
    bbox = BoundingBox(min_lat=48.0, min_lng=16.0, max_lat=48.5, max_lng=16.6)
    query = build_overpass_query(bbox, query_timeout_s=10)
    assert "48.000000,16.000000,48.500000,16.600000" in query
    assert "[out:json][timeout:10]" in query


def test_overpass_query_rejects_zero_timeout() -> None:
    with pytest.raises(ValueError):
        build_overpass_query(VIENNA_BOUNDING_BOX, query_timeout_s=0)


def test_config_rejects_unknown_host() -> None:
    with pytest.raises(ValueError):
        OSMOverpassConfig(
            endpoint="https://evil.example/api/interpreter",
            user_agent="wien-oepnv-test/1.0",
        )


def test_config_rejects_blank_user_agent() -> None:
    with pytest.raises(ValueError):
        OSMOverpassConfig(
            endpoint=DEFAULT_OVERPASS_ENDPOINTS[0],
            user_agent="   ",
        )


def test_config_clamps_oversized_timeout() -> None:
    config = OSMOverpassConfig(
        endpoint=DEFAULT_OVERPASS_ENDPOINTS[0],
        user_agent="wien-oepnv-test/1.0",
        timeout_s=99999.0,
    )
    assert config.timeout_s <= 20.0


# ---------------------------------------------------------------- station mapping


def test_fetch_stations_parses_node_payload(monkeypatch: pytest.MonkeyPatch, reset_breaker: None) -> None:
    payload = {
        "elements": [
            {
                "type": "node",
                "id": 12345,
                "lat": 48.21,
                "lon": 16.37,
                "tags": {
                    "public_transport": "station",
                    "name": "Wien Mitte",
                    "name:de": "Wien Mitte",
                    "railway": "station",
                },
            }
        ]
    }
    monkeypatch.setattr(osm_module, "request_safe", lambda *_args, **_kwargs: _make_response(payload))
    client = OSMOverpassClient(_config())
    stations = client.fetch_stations()
    assert len(stations) == 1
    station = stations[0]
    assert station.osm_type == "node"
    assert station.osm_id == "12345"
    assert station.name == "Wien Mitte"
    assert station.latitude == pytest.approx(48.21)
    assert station.longitude == pytest.approx(16.37)
    assert "station" in station.types
    place = convert_to_place(station)
    assert place.place_id == "osm:node/12345"
    assert place.name == "Wien Mitte"


def test_fetch_stations_uses_center_for_ways(monkeypatch: pytest.MonkeyPatch, reset_breaker: None) -> None:
    payload = {
        "elements": [
            {
                "type": "way",
                "id": 999,
                "center": {"lat": 48.184, "lon": 16.336},
                "tags": {
                    "public_transport": "stop_area",
                    "name": "Wien Meidling",
                },
            }
        ]
    }
    monkeypatch.setattr(osm_module, "request_safe", lambda *_args, **_kwargs: _make_response(payload))
    stations = OSMOverpassClient(_config()).fetch_stations()
    assert len(stations) == 1
    assert stations[0].osm_type == "way"
    assert stations[0].latitude == pytest.approx(48.184)
    assert stations[0].longitude == pytest.approx(16.336)


def test_fetch_stations_skips_entries_without_name(monkeypatch: pytest.MonkeyPatch, reset_breaker: None) -> None:
    payload = {
        "elements": [
            {
                "type": "node",
                "id": 1,
                "lat": 48.21,
                "lon": 16.37,
                "tags": {"railway": "station"},
            },
            {
                "type": "node",
                "id": 2,
                "lat": 48.21,
                "lon": 16.37,
                "tags": {"railway": "station", "name": "Wien Praterstern"},
            },
        ]
    }
    monkeypatch.setattr(osm_module, "request_safe", lambda *_args, **_kwargs: _make_response(payload))
    stations = OSMOverpassClient(_config()).fetch_stations()
    assert [s.name for s in stations] == ["Wien Praterstern"]


def test_fetch_stations_skips_entries_without_coordinates(monkeypatch: pytest.MonkeyPatch, reset_breaker: None) -> None:
    payload = {
        "elements": [
            {
                "type": "node",
                "id": 1,
                "tags": {"railway": "station", "name": "Wien Mitte"},
            },
        ]
    }
    monkeypatch.setattr(osm_module, "request_safe", lambda *_args, **_kwargs: _make_response(payload))
    assert OSMOverpassClient(_config()).fetch_stations() == []


def test_fetch_stations_skips_elements_with_unrelated_tags(monkeypatch: pytest.MonkeyPatch, reset_breaker: None) -> None:
    payload = {
        "elements": [
            {
                "type": "node",
                "id": 7,
                "lat": 48.21,
                "lon": 16.37,
                "tags": {"name": "Schloss Belvedere", "tourism": "attraction"},
            }
        ]
    }
    monkeypatch.setattr(osm_module, "request_safe", lambda *_args, **_kwargs: _make_response(payload))
    assert OSMOverpassClient(_config()).fetch_stations() == []


def test_fetch_stations_drops_outside_bounding_box(monkeypatch: pytest.MonkeyPatch, reset_breaker: None) -> None:
    payload = {
        "elements": [
            {
                "type": "node",
                "id": 8,
                "lat": 47.0,
                "lon": 16.0,
                "tags": {"railway": "station", "name": "Far Away"},
            }
        ]
    }
    monkeypatch.setattr(osm_module, "request_safe", lambda *_args, **_kwargs: _make_response(payload))
    assert OSMOverpassClient(_config()).fetch_stations() == []


def test_fetch_stations_dedupes_repeated_ids(monkeypatch: pytest.MonkeyPatch, reset_breaker: None) -> None:
    payload = {
        "elements": [
            {
                "type": "node",
                "id": 11,
                "lat": 48.21,
                "lon": 16.37,
                "tags": {"railway": "station", "name": "Wien Hauptbahnhof"},
            },
            {
                "type": "node",
                "id": 11,
                "lat": 48.21,
                "lon": 16.37,
                "tags": {"railway": "station", "name": "Wien Hauptbahnhof"},
            },
        ]
    }
    monkeypatch.setattr(osm_module, "request_safe", lambda *_args, **_kwargs: _make_response(payload))
    stations = OSMOverpassClient(_config()).fetch_stations()
    assert len(stations) == 1


def test_filter_complete_places_drops_blank_names() -> None:
    from src.places.client import Place

    full = Place(place_id="osm:node/1", name="Wien", latitude=48.0, longitude=16.0, types=[], formatted_address=None)
    blank = Place(place_id="osm:node/2", name="   ", latitude=48.0, longitude=16.0, types=[], formatted_address=None)
    assert filter_complete_places([full, blank]) == [full]


def test_get_overpass_endpoint_falls_back_on_unknown_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OVERPASS_URL", "https://evil.example/api/interpreter")
    assert get_overpass_endpoint() == DEFAULT_OVERPASS_ENDPOINTS[0]


def test_get_overpass_endpoint_accepts_known_mirror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OVERPASS_URL", DEFAULT_OVERPASS_ENDPOINTS[1])
    assert get_overpass_endpoint() == DEFAULT_OVERPASS_ENDPOINTS[1]


# ---------------------------------------------------------------- error paths


def test_fetch_stations_wraps_request_exception(monkeypatch: pytest.MonkeyPatch, reset_breaker: None) -> None:
    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise requests.ConnectionError("simulated outage")

    monkeypatch.setattr(osm_module, "request_safe", _boom)
    with pytest.raises(OSMOverpassError):
        OSMOverpassClient(_config()).fetch_stations()


def test_fetch_stations_wraps_invalid_json(monkeypatch: pytest.MonkeyPatch, reset_breaker: None) -> None:
    response = MagicMock(spec=requests.Response)
    response.status_code = 200
    response.json.side_effect = ValueError("not JSON")
    monkeypatch.setattr(osm_module, "request_safe", lambda *_a, **_k: response)
    with pytest.raises(OSMOverpassError):
        OSMOverpassClient(_config()).fetch_stations()


def test_fetch_stations_breaker_opens_after_repeated_failures(monkeypatch: pytest.MonkeyPatch, reset_breaker: None) -> None:
    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise requests.ConnectionError("simulated outage")

    monkeypatch.setattr(osm_module, "request_safe", _boom)
    client = OSMOverpassClient(_config())
    for _ in range(osm_module._BREAKER.failure_threshold):
        with pytest.raises(OSMOverpassError):
            client.fetch_stations()

    # Breaker should now be open — next call short-circuits without
    # invoking request_safe.
    def _must_not_be_called(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("breaker open should short-circuit")

    monkeypatch.setattr(osm_module, "request_safe", _must_not_be_called)
    with pytest.raises(OSMOverpassError):
        client.fetch_stations()


def test_fetch_osm_places_returns_place_objects(monkeypatch: pytest.MonkeyPatch, reset_breaker: None) -> None:
    payload = {
        "elements": [
            {
                "type": "node",
                "id": 42,
                "lat": 48.2,
                "lon": 16.4,
                "tags": {"railway": "station", "name": "Wien Floridsdorf"},
            }
        ]
    }
    monkeypatch.setattr(osm_module, "request_safe", lambda *_a, **_k: _make_response(payload))
    places = fetch_osm_places(_config())
    assert len(places) == 1
    assert places[0].name == "Wien Floridsdorf"
    assert places[0].place_id == "osm:node/42"


def test_osm_station_types_is_stable() -> None:
    station = OSMStation(
        osm_id="1",
        osm_type="node",
        name="Test",
        latitude=48.2,
        longitude=16.4,
        tags={"public_transport": "station", "railway": "station"},
    )
    # Order is canonical: public_transport first, then railway, no
    # duplicates even when the values are identical.
    assert station.types == ["station"]
