"""Tests for the S-Bahn Stammstrecke GTFS-Realtime provider.

The suite drives the provider with hand-built ``MagicMock``-shaped
``FeedMessage`` fixtures rather than constructing raw protobuf binary
strings (per the project's protobuf-testing convention) so the
average-delay calculation, threshold logic, and self-heal contract can
be verified without any real network IO and without building real
protobuf wire bytes.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.providers import gtfs_stammstrecke as provider


# ------------------------------------------------------------------ helpers


@pytest.fixture
def reset_breaker() -> Iterator[None]:
    provider._BREAKER.reset()
    try:
        yield
    finally:
        provider._BREAKER.reset()


def _make_stop_time_update(
    stop_id: str,
    *,
    arrival_delay: int | None = None,
    departure_delay: int | None = None,
) -> MagicMock:
    """Return a ``stop_time_update`` shaped exactly like the protobuf API.

    ``arrival`` and ``departure`` mirror the GTFS-RT TripDescriptor sub-
    messages; setting either one to ``None`` matches an unset oneof on
    the wire and exercises the "missing arrival.delay" branch in
    :func:`provider._select_delay_seconds`.
    """
    stu = MagicMock()
    stu.stop_id = stop_id

    if arrival_delay is None:
        stu.arrival = None
    else:
        arrival = MagicMock()
        arrival.delay = arrival_delay
        stu.arrival = arrival

    if departure_delay is None:
        stu.departure = None
    else:
        departure = MagicMock()
        departure.delay = departure_delay
        stu.departure = departure

    return stu


def _make_entity(
    *,
    trip_id: str,
    stop_delays: Sequence[tuple[str, int]],
) -> MagicMock:
    """Return one ``FeedEntity`` MagicMock with a TripUpdate sub-message."""
    entity = MagicMock()
    entity.HasField = lambda _name: True
    trip_update = MagicMock()
    trip = MagicMock()
    trip.trip_id = trip_id
    trip_update.trip = trip
    trip_update.stop_time_update = [_make_stop_time_update(stop_id, arrival_delay=delay) for stop_id, delay in stop_delays]
    entity.trip_update = trip_update
    return entity


def _make_feed(*entities: MagicMock) -> MagicMock:
    feed = MagicMock()
    feed.entity = list(entities)
    return feed


_STAMMSTRECKE_STOP_IDS: tuple[str, ...] = (
    "8100008",  # Floridsdorf
    "8100015",  # Handelskai
    "8100018",  # Praterstern
    "8100050",  # Wien Mitte
    "8100002",  # Hauptbahnhof
    "8100353",  # Meidling
)


# -------------------------------------------------------- normalisation


def test_normalize_station_name_strips_suffix_and_accents() -> None:
    assert provider._normalize_station_name("Wien Hauptbahnhof") == "wien"
    assert provider._normalize_station_name("Wien Mitte") == "wien mitte"
    assert provider._normalize_station_name("Wien Hbf.") == "wien"
    assert provider._normalize_station_name("Wien Praterstern Bf") == "wien praterstern"


# -------------------------------------------------------- delay calculation


def test_average_delay_minutes_returns_zero_for_empty_input() -> None:
    assert provider.calculate_average_delay_minutes([]) == 0.0


def test_average_delay_minutes_clamps_negative_values() -> None:
    delays = [
        provider.CorridorDelay(trip_id="a", delay_seconds=-300, stop_ids=frozenset({"x"})),
        provider.CorridorDelay(trip_id="b", delay_seconds=600, stop_ids=frozenset({"x"})),
    ]
    average = provider.calculate_average_delay_minutes(delays)
    # max(0, -300)=0 and 600s => mean 300s = 5.0 min
    assert average == pytest.approx(5.0)


def test_iter_corridor_delays_finds_trips_touching_corridor() -> None:
    feed = _make_feed(
        _make_entity(trip_id="trip-1", stop_delays=[("8100008", 720), ("8100050", 600)]),
        _make_entity(trip_id="trip-2", stop_delays=[("99999", 60)]),
    )
    delays = provider.iter_corridor_delays(feed, _STAMMSTRECKE_STOP_IDS)
    assert {d.trip_id for d in delays} == {"trip-1"}
    # Worst delay across corridor stops is 720s.
    assert delays[0].delay_seconds == 720


def test_iter_corridor_delays_uses_max_abs_arrival_or_departure() -> None:
    entity = MagicMock()
    entity.HasField = lambda _name: True
    trip = MagicMock()
    trip.trip_id = "trip-3"
    trip_update = MagicMock()
    trip_update.trip = trip
    stu = _make_stop_time_update("8100050", arrival_delay=60, departure_delay=540)
    trip_update.stop_time_update = [stu]
    entity.trip_update = trip_update

    feed = _make_feed(entity)
    delays = provider.iter_corridor_delays(feed, _STAMMSTRECKE_STOP_IDS)
    assert len(delays) == 1
    # 540s departure delay wins over 60s arrival delay (larger abs).
    assert delays[0].delay_seconds == 540


def test_iter_corridor_delays_skips_trips_with_only_unrelated_stops() -> None:
    feed = _make_feed(
        _make_entity(trip_id="trip-x", stop_delays=[("99999", 600)]),
    )
    assert provider.iter_corridor_delays(feed, _STAMMSTRECKE_STOP_IDS) == []


def test_iter_corridor_delays_handles_empty_corridor_set() -> None:
    feed = _make_feed(
        _make_entity(trip_id="trip-1", stop_delays=[("8100008", 600)]),
    )
    assert provider.iter_corridor_delays(feed, frozenset()) == []


# -------------------------------------------------------- threshold logic


def test_evaluate_corridor_yields_event_when_average_above_threshold(
    monkeypatch: pytest.MonkeyPatch, reset_breaker: None, tmp_path: Path
) -> None:
    feed = _make_feed(
        _make_entity(trip_id="trip-1", stop_delays=[("8100008", 720)]),  # 12 min
        _make_entity(trip_id="trip-2", stop_delays=[("8100050", 660)]),  # 11 min  → mean = 11.5 min
    )

    def _mapping(*_args: object, **_kwargs: object) -> dict[str, frozenset[str]]:
        return {
            "Wien Floridsdorf": frozenset({"8100008"}),
            "Wien Mitte": frozenset({"8100050"}),
        }

    monkeypatch.setattr(provider, "load_stop_id_index", _mapping)
    monkeypatch.setattr(provider, "_fetch_blob", lambda *_a, **_k: b"opaque-blob")
    monkeypatch.setattr(provider, "parse_feed_message", lambda blob: feed)

    events = provider.fetch_events(stops_path=tmp_path / "stops.txt")
    assert len(events) == 1
    title = events[0]["title"]
    # mean = 11.5 min → rounded = 12
    assert "12 Minuten" in title
    assert title.startswith("S-Bahn Stammstrecke:")


def test_evaluate_corridor_returns_empty_at_or_below_threshold(
    monkeypatch: pytest.MonkeyPatch, reset_breaker: None, tmp_path: Path
) -> None:
    feed = _make_feed(
        _make_entity(trip_id="trip-1", stop_delays=[("8100008", 540)]),  # 9 min — equals threshold
    )

    def _mapping(*_args: object, **_kwargs: object) -> dict[str, frozenset[str]]:
        return {"Wien Floridsdorf": frozenset({"8100008"})}

    monkeypatch.setattr(provider, "load_stop_id_index", _mapping)
    monkeypatch.setattr(provider, "_fetch_blob", lambda *_a, **_k: b"opaque-blob")
    monkeypatch.setattr(provider, "parse_feed_message", lambda blob: feed)

    assert provider.fetch_events(stops_path=tmp_path / "stops.txt") == []


def test_evaluate_corridor_returns_empty_when_no_active_trips(monkeypatch: pytest.MonkeyPatch, reset_breaker: None, tmp_path: Path) -> None:
    feed = _make_feed(
        _make_entity(trip_id="trip-x", stop_delays=[("99999", 600)]),  # outside corridor
    )

    def _mapping(*_args: object, **_kwargs: object) -> dict[str, frozenset[str]]:
        return {"Wien Floridsdorf": frozenset({"8100008"})}

    monkeypatch.setattr(provider, "load_stop_id_index", _mapping)
    monkeypatch.setattr(provider, "_fetch_blob", lambda *_a, **_k: b"opaque-blob")
    monkeypatch.setattr(provider, "parse_feed_message", lambda blob: feed)

    assert provider.fetch_events(stops_path=tmp_path / "stops.txt") == []


def test_evaluate_corridor_self_heals(monkeypatch: pytest.MonkeyPatch, reset_breaker: None, tmp_path: Path) -> None:
    """First call: 11 min avg → emits alert. Second call: 0 min → empty."""
    feeds = iter(
        [
            _make_feed(_make_entity(trip_id="trip-1", stop_delays=[("8100008", 660)])),
            _make_feed(_make_entity(trip_id="trip-1", stop_delays=[("8100008", 0)])),
        ]
    )

    def _mapping(*_args: object, **_kwargs: object) -> dict[str, frozenset[str]]:
        return {"Wien Floridsdorf": frozenset({"8100008"})}

    monkeypatch.setattr(provider, "load_stop_id_index", _mapping)
    monkeypatch.setattr(provider, "_fetch_blob", lambda *_a, **_k: b"opaque-blob")
    monkeypatch.setattr(provider, "parse_feed_message", lambda blob: next(feeds))

    first = provider.fetch_events(stops_path=tmp_path / "stops.txt")
    assert len(first) == 1
    second = provider.fetch_events(stops_path=tmp_path / "stops.txt")
    assert second == []


# -------------------------------------------------------- resilience


def test_fetch_events_returns_empty_on_malformed_payload(monkeypatch: pytest.MonkeyPatch, reset_breaker: None, tmp_path: Path) -> None:
    def _mapping(*_args: object, **_kwargs: object) -> dict[str, frozenset[str]]:
        return {"Wien Floridsdorf": frozenset({"8100008"})}

    def _explode(_blob: bytes) -> Any:
        raise ValueError("Could not parse GTFS-RT FeedMessage: simulated")

    monkeypatch.setattr(provider, "load_stop_id_index", _mapping)
    monkeypatch.setattr(provider, "_fetch_blob", lambda *_a, **_k: b"\x00\xff garbage")
    monkeypatch.setattr(provider, "parse_feed_message", _explode)

    assert provider.fetch_events(stops_path=tmp_path / "stops.txt") == []


def test_fetch_events_returns_empty_when_blob_unavailable(monkeypatch: pytest.MonkeyPatch, reset_breaker: None, tmp_path: Path) -> None:
    def _mapping(*_args: object, **_kwargs: object) -> dict[str, frozenset[str]]:
        return {"Wien Floridsdorf": frozenset({"8100008"})}

    monkeypatch.setattr(provider, "load_stop_id_index", _mapping)
    monkeypatch.setattr(provider, "_fetch_blob", lambda *_a, **_k: None)
    assert provider.fetch_events(stops_path=tmp_path / "stops.txt") == []


def test_fetch_events_returns_empty_when_no_corridor_mapping(monkeypatch: pytest.MonkeyPatch, reset_breaker: None, tmp_path: Path) -> None:
    monkeypatch.setattr(
        provider,
        "load_stop_id_index",
        lambda *_args, **_kwargs: {name: frozenset() for name in provider.STAMMSTRECKE_STATION_NAMES},
    )

    # Even though _fetch_blob would return data, no corridor stops means
    # we short-circuit before any network call.
    def _must_not_fetch(*_a: object, **_k: object) -> bytes | None:
        raise AssertionError("must not fetch when corridor empty")

    monkeypatch.setattr(provider, "_fetch_blob", _must_not_fetch)
    assert provider.fetch_events(stops_path=tmp_path / "stops.txt") == []


def test_fetch_events_returns_empty_when_breaker_open(monkeypatch: pytest.MonkeyPatch, reset_breaker: None, tmp_path: Path) -> None:
    def _mapping(*_args: object, **_kwargs: object) -> dict[str, frozenset[str]]:
        return {"Wien Floridsdorf": frozenset({"8100008"})}

    def _failing(*_a: object, **_k: object) -> None:
        raise RuntimeError("simulated upstream outage")

    monkeypatch.setattr(provider, "load_stop_id_index", _mapping)
    monkeypatch.setattr(provider, "_fetch_blob", _failing)

    # Trip the breaker.
    threshold = provider._BREAKER.failure_threshold
    for _ in range(threshold):
        events = provider.fetch_events(stops_path=tmp_path / "stops.txt")
        assert events == []

    # Subsequent call short-circuits without invoking _fetch_blob.
    def _must_not_fetch_when_open(*_a: object, **_k: object) -> bytes | None:
        raise AssertionError("breaker open should short-circuit")

    monkeypatch.setattr(provider, "_fetch_blob", _must_not_fetch_when_open)
    assert provider.fetch_events(stops_path=tmp_path / "stops.txt") == []


# -------------------------------------------------------- stop_id index


def test_load_stop_id_index_resolves_known_stations(tmp_path: Path) -> None:
    stops_txt = tmp_path / "stops.txt"
    stops_txt.write_text(
        "stop_id,stop_name,stop_lat,stop_lon,location_type\n"
        "8100008,Wien Floridsdorf Bf,48.256,16.401,1\n"
        "8100050,Wien Mitte Bahnhof,48.207,16.385,1\n"
        "8100050:1,Wien Mitte Bahnhof S-Bahn,48.207,16.385,0\n"
        "9999999,Some Other Town,48.999,16.999,1\n",
        encoding="utf-8",
    )
    index = provider.load_stop_id_index(stops_txt)
    assert index["Wien Floridsdorf"] == frozenset({"8100008"})
    assert index["Wien Mitte"] == frozenset({"8100050", "8100050:1"})
    assert index["Wien Hauptbahnhof"] == frozenset()


def test_load_stop_id_index_returns_empty_sets_when_file_missing(
    tmp_path: Path,
) -> None:
    index = provider.load_stop_id_index(tmp_path / "missing.txt")
    assert all(values == frozenset() for values in index.values())
    assert set(index.keys()) == set(provider.STAMMSTRECKE_STATION_NAMES)


# -------------------------------------------------------- event shape


def test_build_event_renders_expected_title() -> None:
    snapshot = provider.StammstreckeStateSnapshot(
        average_delay_minutes=11.4,
        active_trips=3,
    )
    item = provider.build_event(snapshot, link="https://example.org")
    assert item["title"] == "S-Bahn Stammstrecke: Derzeit durchschnittlich 11 Minuten Verspätung"
    assert "S-Bahn-Stammstrecke" in item["description"]
    snapshot2 = provider.StammstreckeStateSnapshot(
        average_delay_minutes=12.6,
        active_trips=2,
    )
    item2 = provider.build_event(snapshot2, link="https://example.org")
    assert item2["title"] == "S-Bahn Stammstrecke: Derzeit durchschnittlich 13 Minuten Verspätung"
