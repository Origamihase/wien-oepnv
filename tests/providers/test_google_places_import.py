from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, List, Iterator, Any
from unittest.mock import MagicMock

import pytest

from src.places.client import GooglePlacesClient, GooglePlacesConfig, Place
from src.places.merge import BoundingBox, MergeConfig, merge_places
from src.places.normalize import haversine_m, normalize_name
from src.places.tiling import Tile


def make_place(
    place_id: str,
    name: str,
    *,
    lat: float,
    lng: float,
    types: Iterable[str] | None = None,
    address: str | None = None,
) -> Place:
    return Place(
        place_id=place_id,
        name=name,
        latitude=lat,
        longitude=lng,
        types=list(types or []),
        formatted_address=address,
    )


def _data_path(name: str) -> Path:
    return Path(__file__).resolve().parent / "data" / name


def load_existing() -> List[dict]:
    path = _data_path("stations_existing.json")
    return json.loads(path.read_text(encoding="utf-8"))


def load_expected() -> List[dict]:
    path = _data_path("stations_expected.json")
    return json.loads(path.read_text(encoding="utf-8"))


def test_normalize_name_handles_accents_and_spacing() -> None:
    assert normalize_name("  Wíen   Mítte  ") == "wien mitte"


def test_haversine_distance_matches_reference() -> None:
    distance = haversine_m(48.2065, 16.384, 48.207, 16.3845)
    assert distance == pytest.approx(66.8129884753474, rel=1e-6)


def test_merge_updates_existing_by_name() -> None:
    existing = load_existing()
    places = [
        make_place(
            "place-wien-mitte",
            "wien mitte",
            lat=48.207,
            lng=16.3845,
            types=["train_station"],
            address="1030 Wien",
        )
    ]
    config = MergeConfig(max_distance_m=150.0, bounding_box=None)
    outcome = merge_places(existing, places, config)
    assert len(outcome.new_entries) == 0
    assert len(outcome.updated_entries) == 1
    updated = outcome.updated_entries[0]
    assert updated["_google_place_id"] == "place-wien-mitte"
    assert "google_places" in updated["source"]


def test_merge_matches_by_distance() -> None:
    existing = load_existing()
    places = [
        make_place(
            "alt-id",
            "Bahnhof Landstraße",
            lat=48.2066,
            lng=16.3844,
            types=["bus_station"],
        )
    ]
    config = MergeConfig(max_distance_m=150.0, bounding_box=None)
    outcome = merge_places(existing, places, config)
    assert len(outcome.new_entries) == 0
    assert len(outcome.updated_entries) == 1
    updated = outcome.updated_entries[0]
    assert updated.get("_google_place_id") == "alt-id"


def test_merge_infers_in_vienna_from_address_and_bounds() -> None:
    existing: List[dict] = []
    places = [
        make_place(
            "vienna-place",
            "Vienna Central",
            lat=48.22,
            lng=16.38,
            address="Irgendwas, Wien",
        ),
        make_place(
            "vienna-bounds",
            "Bounds Station",
            lat=48.25,
            lng=16.45,
        ),
        make_place(
            "burgenland",
            "Eisenstadt",
            lat=47.85,
            lng=16.52,
            address="Eisenstadt, Österreich",
        ),
    ]
    bounds = BoundingBox(min_lat=48.2, min_lng=16.3, max_lat=48.4, max_lng=16.6)
    config = MergeConfig(max_distance_m=150.0, bounding_box=bounds)
    outcome = merge_places(existing, places, config)
    vienna = [entry for entry in outcome.new_entries if entry["name"] != "Eisenstadt"]
    assert all(entry["in_vienna"] for entry in vienna)
    burgenland_entry = next(entry for entry in outcome.new_entries if entry["name"] == "Eisenstadt")
    assert burgenland_entry["in_vienna"] is False


class DummyResponse:
    def __init__(self, status: int, payload: dict | None = None) -> None:
        self.status_code = status
        self._payload = payload or {}
        self.text = json.dumps(self._payload)
        self.headers: dict = {}
        self.raw = MagicMock()
        self.raw.connection.sock.getpeername.return_value = ("8.8.8.8", 443)

    def json(self) -> dict:
        return self._payload

    def iter_content(self, chunk_size: int = 1) -> Iterator[bytes]:
        yield self.text.encode("utf-8")

    def close(self) -> None:
        pass

    def __enter__(self) -> DummyResponse:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


class DummySession:
    def __init__(self, responses: List[DummyResponse]) -> None:
        self._responses = responses
        self.calls: List[dict] = []

    def post(self, url: str, *, headers: dict, json: dict, timeout: float, **kwargs: Any) -> DummyResponse:
        if not self._responses:
            raise AssertionError("No more responses queued")
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return self._responses.pop(0)


def test_client_handles_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = [
        DummyResponse(
            200,
            {
                "places": [
                    {
                        "id": "first",
                        "displayName": {"text": "First"},
                        "location": {"latitude": 48.2, "longitude": 16.3},
                        "types": ["train_station"],
                    }
                ],
                "nextPageToken": "token123",
            },
        ),
        DummyResponse(
            200,
            {
                "places": [
                    {
                        "id": "second",
                        "displayName": {"text": "Second"},
                        "location": {"latitude": 48.21, "longitude": 16.31},
                        "types": ["subway_station"],
                        "formattedAddress": "Vienna",
                    }
                ]
            },
        ),
    ]
    session = DummySession(responses)
    config = GooglePlacesConfig(
        api_key="key",
        included_types=["train_station"],
        language="de",
        region="AT",
        radius_m=1000,
        timeout_s=5.0,
        max_retries=0,
    )
    client = GooglePlacesClient(config, session=session)
    results = list(client.iter_nearby([Tile(latitude=48.2, longitude=16.3)]))
    assert {place.place_id for place in results} == {"first", "second"}
    assert client.request_count == 2
    assert session.calls[0]["json"].get("pageToken") is None
    assert session.calls[1]["json"].get("pageToken") == "token123"


def test_client_retries_on_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = [
        DummyResponse(429, {"error": "rate limit"}),
        DummyResponse(
            200,
            {
                "places": [],
            },
        ),
    ]
    session = DummySession(responses)
    config = GooglePlacesConfig(
        api_key="key",
        included_types=["train_station"],
        language="de",
        region="AT",
        radius_m=1000,
        timeout_s=5.0,
        max_retries=1,
    )
    client = GooglePlacesClient(config, session=session)
    monkeypatch.setattr("src.places.client.time.sleep", lambda _: None)
    monkeypatch.setattr(client, "_backoff", lambda attempt: 0.0)
    results = list(client.iter_nearby([Tile(latitude=48.2, longitude=16.3)]))
    assert results == []
    assert client.request_count == 2


def test_merge_golden_file() -> None:
    existing = load_existing()
    places = [
        make_place(
            "place-wien-mitte",
            "Wien Mitte",
            lat=48.207,
            lng=16.3845,
            types=["train_station", "bus_station"],
            address="Landstraße, 1030 Wien",
        ),
        make_place(
            "place-wien-landstrasse",
            "Bahnhof Landstraße",
            lat=48.2066,
            lng=16.3844,
            types=["train_station"],
            address="Landstraße, 1030 Wien",
        ),
        make_place(
            "place-mattersburg",
            "Mattersburg Bahnhof",
            lat=47.743,
            lng=16.4,
            types=["train_station"],
            address="7210 Mattersburg, Österreich",
        ),
    ]
    bounds = BoundingBox(min_lat=48.1, min_lng=16.1, max_lat=48.4, max_lng=16.6)
    config = MergeConfig(max_distance_m=150.0, bounding_box=bounds)
    outcome = merge_places(existing, places, config)
    assert outcome.stations == load_expected()
