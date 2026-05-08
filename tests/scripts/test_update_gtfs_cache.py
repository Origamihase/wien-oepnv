"""Tests for ``scripts/update_gtfs_cache.py``.

The suite exercises the threshold + first-seen state machine and the
network-fetch wrapper.  Live HTTP is stubbed via ``monkeypatch`` so
the suite stays fully offline.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from scripts import update_gtfs_cache as updater
from src.providers.gtfs_stammstrecke import (
    STAMMSTRECKE_STATION_NAMES,
    STAMMSTRECKE_THRESHOLD_MINUTES,
)


VIENNA = ZoneInfo("Europe/Vienna")


# ------------------------------------------------------------------ helpers


@pytest.fixture
def reset_breaker() -> Iterator[None]:
    updater._BREAKER.reset()
    try:
        yield
    finally:
        updater._BREAKER.reset()


def _make_stop_time_update(
    stop_id: str,
    *,
    arrival_delay: int | None = None,
    departure_delay: int | None = None,
) -> MagicMock:
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


def _make_entity(*, trip_id: str, stop_delays: Sequence[tuple[str, int]]) -> MagicMock:
    entity = MagicMock()
    entity.HasField = lambda _name: True
    trip_update = MagicMock()
    trip = MagicMock()
    trip.trip_id = trip_id
    trip_update.trip = trip
    trip_update.stop_time_update = [
        _make_stop_time_update(stop_id, arrival_delay=delay)
        for stop_id, delay in stop_delays
    ]
    entity.trip_update = trip_update
    return entity


def _make_feed(*entities: MagicMock) -> MagicMock:
    feed = MagicMock()
    feed.entity = list(entities)
    return feed


# -------------------------------------------------------- pure helpers


def test_normalize_station_name_strips_suffix_and_accents() -> None:
    assert updater.normalize_station_name("Wien Hauptbahnhof") == "wien"
    assert updater.normalize_station_name("Wien Mitte") == "wien mitte"
    assert updater.normalize_station_name("Wien Hbf.") == "wien"


def test_calculate_average_delay_minutes_clamps_negative() -> None:
    delays = [
        updater.CorridorDelay(trip_id="a", delay_seconds=-300, stop_ids=frozenset({"x"})),
        updater.CorridorDelay(trip_id="b", delay_seconds=600, stop_ids=frozenset({"x"})),
    ]
    assert updater.calculate_average_delay_minutes(delays) == pytest.approx(5.0)


def test_calculate_average_delay_minutes_returns_zero_for_empty() -> None:
    assert updater.calculate_average_delay_minutes([]) == 0.0


def test_iter_corridor_delays_filters_unrelated_trips() -> None:
    feed = _make_feed(
        _make_entity(trip_id="trip-1", stop_delays=[("8100008", 720), ("8100050", 600)]),
        _make_entity(trip_id="trip-2", stop_delays=[("99999", 60)]),
    )
    delays = updater.iter_corridor_delays(feed, ("8100008", "8100050"))
    assert {d.trip_id for d in delays} == {"trip-1"}
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
    delays = updater.iter_corridor_delays(feed, ("8100050",))
    assert len(delays) == 1
    assert delays[0].delay_seconds == 540


def test_iter_corridor_delays_handles_empty_corridor_set() -> None:
    feed = _make_feed(
        _make_entity(trip_id="trip-1", stop_delays=[("8100008", 600)]),
    )
    assert updater.iter_corridor_delays(feed, frozenset()) == []


# -------------------------------------------------------- endpoint resolver


def test_resolve_endpoint_uses_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OEBB_GTFS_RT_URL", raising=False)
    assert updater.resolve_endpoint() == updater.DEFAULT_GTFS_RT_URL


def test_resolve_endpoint_rejects_untrusted_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OEBB_GTFS_RT_URL", "https://evil.example.com/feed")
    assert updater.resolve_endpoint() == updater.DEFAULT_GTFS_RT_URL


def test_resolve_endpoint_accepts_trusted_host(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = "https://data.oebb.at/gtfs-rt/tripUpdates"
    monkeypatch.setenv("OEBB_GTFS_RT_URL", raw)
    assert updater.resolve_endpoint() == raw


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
    index = updater.load_stop_id_index(stops_txt)
    assert index["Wien Floridsdorf"] == frozenset({"8100008"})
    assert index["Wien Mitte"] == frozenset({"8100050", "8100050:1"})
    assert index["Wien Hauptbahnhof"] == frozenset()


def test_load_stop_id_index_returns_empty_when_file_missing(tmp_path: Path) -> None:
    index = updater.load_stop_id_index(tmp_path / "missing.txt")
    assert all(values == frozenset() for values in index.values())


# -------------------------------------------------------- state machine


def test_compute_next_state_clears_when_below_threshold() -> None:
    snapshot = updater.StammstreckeStateSnapshot(
        average_delay_minutes=STAMMSTRECKE_THRESHOLD_MINUTES,
        active_trips=2,
    )
    now = datetime(2026, 5, 8, 12, 0, tzinfo=VIENNA)
    document = updater.compute_next_state(snapshot, existing_document=None, now=now)
    assert document["events"] == []
    assert document["metadata"]["last_run"] == now.isoformat()
    assert document["metadata"]["version"] == updater.CACHE_DOCUMENT_VERSION


def test_compute_next_state_creates_new_event_when_above_threshold_with_no_prior() -> None:
    snapshot = updater.StammstreckeStateSnapshot(
        average_delay_minutes=11.6,
        active_trips=4,
    )
    now = datetime(2026, 5, 8, 12, 0, tzinfo=VIENNA)
    document = updater.compute_next_state(snapshot, existing_document=None, now=now)
    assert len(document["events"]) == 1
    event = document["events"][0]
    assert event["first_seen"] == now.isoformat()
    assert event["updated"] == now.isoformat()
    assert event["average_delay_minutes"] == 12  # rounded
    assert event["active_trips"] == 4
    assert isinstance(event["guid"], str) and len(event["guid"]) == 64


def test_compute_next_state_preserves_first_seen_when_event_active() -> None:
    snapshot = updater.StammstreckeStateSnapshot(
        average_delay_minutes=15.2,
        active_trips=8,
    )
    earlier = datetime(2026, 5, 8, 8, 0, tzinfo=VIENNA).isoformat()
    later = datetime(2026, 5, 8, 12, 30, tzinfo=VIENNA)
    existing = {
        "events": [
            {
                "guid": "preserved-guid",
                "first_seen": earlier,
                "updated": "2026-05-08T11:30:00+02:00",
                "average_delay_minutes": 11,
                "active_trips": 5,
            }
        ],
        "metadata": {"last_run": "2026-05-08T11:30:00+02:00"},
    }
    document = updater.compute_next_state(snapshot, existing_document=existing, now=later)
    event = document["events"][0]
    assert event["first_seen"] == earlier  # preserved
    assert event["updated"] == later.isoformat()  # bumped
    assert event["guid"] == "preserved-guid"  # preserved
    assert event["average_delay_minutes"] == 15  # rounded from 15.2
    assert event["active_trips"] == 8


def test_compute_next_state_strict_threshold_at_nine_minutes_clears() -> None:
    """The contract requires *strictly greater than* nine minutes."""
    snapshot = updater.StammstreckeStateSnapshot(
        average_delay_minutes=9.0,
        active_trips=3,
    )
    now = datetime(2026, 5, 8, 12, 0, tzinfo=VIENNA)
    document = updater.compute_next_state(snapshot, existing_document=None, now=now)
    assert document["events"] == []


def test_compute_next_state_above_threshold_emits_event_just_above() -> None:
    snapshot = updater.StammstreckeStateSnapshot(
        average_delay_minutes=9.5,
        active_trips=1,
    )
    now = datetime(2026, 5, 8, 12, 0, tzinfo=VIENNA)
    document = updater.compute_next_state(snapshot, existing_document=None, now=now)
    assert len(document["events"]) == 1


def test_compute_next_state_clears_active_event_when_recovered() -> None:
    snapshot = updater.StammstreckeStateSnapshot(
        average_delay_minutes=4.0,
        active_trips=2,
    )
    existing = {
        "events": [
            {
                "guid": "preserved-guid",
                "first_seen": "2026-05-08T08:00:00+02:00",
                "updated": "2026-05-08T11:30:00+02:00",
                "average_delay_minutes": 11,
                "active_trips": 5,
            }
        ],
        "metadata": {"last_run": "2026-05-08T11:30:00+02:00"},
    }
    now = datetime(2026, 5, 8, 12, 0, tzinfo=VIENNA)
    document = updater.compute_next_state(snapshot, existing_document=existing, now=now)
    assert document["events"] == []


def test_write_state_round_trips_via_load_existing_state(tmp_path: Path) -> None:
    document = {
        "events": [
            {
                "guid": "g",
                "first_seen": "2026-05-08T08:00:00+02:00",
                "updated": "2026-05-08T12:00:00+02:00",
                "average_delay_minutes": 12,
                "active_trips": 5,
            }
        ],
        "metadata": {"last_run": "2026-05-08T12:00:00+02:00", "version": 1},
    }
    cache_path = tmp_path / "cache" / "gtfs_stammstrecke" / "events.json"
    updater.write_state(cache_path, document)
    assert cache_path.exists()
    loaded = updater.load_existing_state(cache_path)
    assert loaded == document


# -------------------------------------------------------- run_update orchestration


def _stub_stop_index(monkeypatch: pytest.MonkeyPatch, mapping: dict[str, frozenset[str]]) -> None:
    monkeypatch.setattr(
        updater,
        "load_stop_id_index",
        lambda *_args, **_kwargs: mapping,
    )


def test_run_update_persists_active_event_when_threshold_exceeded(
    monkeypatch: pytest.MonkeyPatch, reset_breaker: None, tmp_path: Path
) -> None:
    _stub_stop_index(monkeypatch, {"Wien Floridsdorf": frozenset({"8100008"})})
    monkeypatch.setattr(updater, "fetch_blob", lambda *_a, **_k: b"opaque")
    feed = _make_feed(
        _make_entity(trip_id="trip-1", stop_delays=[("8100008", 660)]),
        _make_entity(trip_id="trip-2", stop_delays=[("8100008", 660)]),
    )
    monkeypatch.setattr(updater, "parse_feed_message", lambda _blob: feed)

    cache_path = tmp_path / "cache.json"
    now = datetime(2026, 5, 8, 12, 0, tzinfo=VIENNA)
    exit_code = updater.run_update(
        cache_path=cache_path,
        url="https://realtime.oebb.at/gtfs-rt/tripUpdates",
        now=now,
    )
    assert exit_code == 0

    document = json.loads(cache_path.read_text(encoding="utf-8"))
    assert len(document["events"]) == 1
    event = document["events"][0]
    assert event["first_seen"] == now.isoformat()
    assert event["updated"] == now.isoformat()
    assert event["average_delay_minutes"] == 11  # 660s == 11.0 min rounds to 11
    assert event["active_trips"] == 2


def test_run_update_preserves_first_seen_across_consecutive_runs(
    monkeypatch: pytest.MonkeyPatch, reset_breaker: None, tmp_path: Path
) -> None:
    _stub_stop_index(monkeypatch, {"Wien Floridsdorf": frozenset({"8100008"})})
    monkeypatch.setattr(updater, "fetch_blob", lambda *_a, **_k: b"opaque")

    feeds = iter(
        [
            _make_feed(_make_entity(trip_id="trip-1", stop_delays=[("8100008", 720)])),
            _make_feed(_make_entity(trip_id="trip-1", stop_delays=[("8100008", 900)])),
        ]
    )
    monkeypatch.setattr(updater, "parse_feed_message", lambda _blob: next(feeds))

    cache_path = tmp_path / "cache.json"
    first_run = datetime(2026, 5, 8, 8, 0, tzinfo=VIENNA)
    second_run = datetime(2026, 5, 8, 9, 0, tzinfo=VIENNA)

    assert updater.run_update(
        cache_path=cache_path,
        url="https://realtime.oebb.at/gtfs-rt/tripUpdates",
        now=first_run,
    ) == 0
    first_doc = json.loads(cache_path.read_text(encoding="utf-8"))

    assert updater.run_update(
        cache_path=cache_path,
        url="https://realtime.oebb.at/gtfs-rt/tripUpdates",
        now=second_run,
    ) == 0
    second_doc = json.loads(cache_path.read_text(encoding="utf-8"))

    assert first_doc["events"][0]["first_seen"] == first_run.isoformat()
    assert second_doc["events"][0]["first_seen"] == first_run.isoformat()  # preserved
    assert second_doc["events"][0]["updated"] == second_run.isoformat()  # bumped
    assert first_doc["events"][0]["guid"] == second_doc["events"][0]["guid"]  # preserved


def test_run_update_clears_events_when_recovered(
    monkeypatch: pytest.MonkeyPatch, reset_breaker: None, tmp_path: Path
) -> None:
    _stub_stop_index(monkeypatch, {"Wien Floridsdorf": frozenset({"8100008"})})
    monkeypatch.setattr(updater, "fetch_blob", lambda *_a, **_k: b"opaque")

    feeds = iter(
        [
            _make_feed(_make_entity(trip_id="trip-1", stop_delays=[("8100008", 720)])),
            _make_feed(_make_entity(trip_id="trip-1", stop_delays=[("8100008", 60)])),
        ]
    )
    monkeypatch.setattr(updater, "parse_feed_message", lambda _blob: next(feeds))

    cache_path = tmp_path / "cache.json"
    assert updater.run_update(
        cache_path=cache_path,
        url="https://realtime.oebb.at/gtfs-rt/tripUpdates",
        now=datetime(2026, 5, 8, 8, 0, tzinfo=VIENNA),
    ) == 0
    assert json.loads(cache_path.read_text(encoding="utf-8"))["events"]

    assert updater.run_update(
        cache_path=cache_path,
        url="https://realtime.oebb.at/gtfs-rt/tripUpdates",
        now=datetime(2026, 5, 8, 9, 0, tzinfo=VIENNA),
    ) == 0
    document = json.loads(cache_path.read_text(encoding="utf-8"))
    assert document["events"] == []


def test_run_update_returns_one_when_corridor_is_empty(
    monkeypatch: pytest.MonkeyPatch, reset_breaker: None, tmp_path: Path
) -> None:
    _stub_stop_index(monkeypatch, {name: frozenset() for name in STAMMSTRECKE_STATION_NAMES})

    def _must_not_fetch(*_a: object, **_k: object) -> bytes | None:
        raise AssertionError("must not fetch when corridor empty")

    monkeypatch.setattr(updater, "fetch_blob", _must_not_fetch)
    cache_path = tmp_path / "cache.json"
    assert updater.run_update(cache_path=cache_path) == 1
    assert not cache_path.exists()


def test_run_update_returns_one_on_empty_blob(
    monkeypatch: pytest.MonkeyPatch, reset_breaker: None, tmp_path: Path
) -> None:
    _stub_stop_index(monkeypatch, {"Wien Floridsdorf": frozenset({"8100008"})})
    monkeypatch.setattr(updater, "fetch_blob", lambda *_a, **_k: None)
    cache_path = tmp_path / "cache.json"
    assert updater.run_update(cache_path=cache_path) == 1
    assert not cache_path.exists()


def test_run_update_returns_one_on_malformed_payload(
    monkeypatch: pytest.MonkeyPatch, reset_breaker: None, tmp_path: Path
) -> None:
    _stub_stop_index(monkeypatch, {"Wien Floridsdorf": frozenset({"8100008"})})
    monkeypatch.setattr(updater, "fetch_blob", lambda *_a, **_k: b"garbage")

    def _explode(_blob: bytes) -> Any:
        raise ValueError("simulated decode failure")

    monkeypatch.setattr(updater, "parse_feed_message", _explode)
    cache_path = tmp_path / "cache.json"
    assert updater.run_update(cache_path=cache_path) == 1
    assert not cache_path.exists()


def test_run_update_returns_one_when_breaker_open(
    monkeypatch: pytest.MonkeyPatch, reset_breaker: None, tmp_path: Path
) -> None:
    _stub_stop_index(monkeypatch, {"Wien Floridsdorf": frozenset({"8100008"})})

    def _failing(*_a: object, **_k: object) -> None:
        raise RuntimeError("simulated upstream outage")

    monkeypatch.setattr(updater, "fetch_blob", _failing)
    threshold = updater._BREAKER.failure_threshold
    cache_path = tmp_path / "cache.json"
    for _ in range(threshold):
        assert updater.run_update(cache_path=cache_path) == 1

    def _must_not_be_called(*_a: object, **_k: object) -> bytes | None:
        raise AssertionError("breaker open should short-circuit")

    monkeypatch.setattr(updater, "fetch_blob", _must_not_be_called)
    assert updater.run_update(cache_path=cache_path) == 1
