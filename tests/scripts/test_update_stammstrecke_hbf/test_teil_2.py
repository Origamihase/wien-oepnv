"""Teil 2 der Tests aus tests/scripts/test_update_stammstrecke_hbf.py.

Beschreibung in __init__.py.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

import pytest
import requests

from scripts import update_stammstrecke_hbf as script  # noqa: E402
from scripts import update_stammstrecke_status as _legacy_script  # noqa: E402
from src.stammstrecke import hbf_board  # noqa: E402

from ._support import (
    REPO_ROOT,
    VIENNA_TZ,
    _dep,
)


def test_collect_drops_when_track_missing() -> None:
    """No ``track``/``rtTrack`` ⇒ conservative drop + counter."""

    departures = [
        _dep(name="S 1", direction="Wien Meidling", track=None),
    ]
    by_direction, diag = script._collect_hbf_observations(departures)
    assert all(not v for v in by_direction.values())
    assert diag.dropped_no_track == 1
    assert diag.dropped_non_stammstrecke_track == 0


def test_collect_rttrack_overrides_scheduled() -> None:
    """Scheduled Bahnsteig 1, but rtTrack moved the train to track 5 → drop.

    Mid-disruption platform change semantics: the realtime track is
    authoritative for the Stammstrecke gate. A train originally
    scheduled on Bahnsteig 1 that VAO announces has been moved to
    track 5 mid-tick must NOT contribute to the Stammstrecke sample
    (the platform change implies the train isn't using the
    Stammstrecke right now).
    """

    departures = [
        _dep(name="S 1", direction="Wien Floridsdorf", track="1", rt_track="5"),
    ]
    by_direction, diag = script._collect_hbf_observations(departures)
    assert all(not v for v in by_direction.values())
    assert diag.dropped_non_stammstrecke_track == 1


def test_collect_rttrack_recovers_to_stammstrecke() -> None:
    """Scheduled track 5, but rtTrack moved to Bahnsteig 2 → keep.

    The mirror image of the override-to-drop case: a platform change
    that BRINGS a train onto the Stammstrecke is honoured the same
    way. Realtime data wins.
    """

    departures = [
        _dep(name="S 2", direction="Mödling", track="5", rt_track="2"),
    ]
    by_direction, diag = script._collect_hbf_observations(departures)
    assert len(by_direction[script.DIRECTION_LABEL_SOUTHBOUND]) == 1
    assert diag.dropped_non_stammstrecke_track == 0


def test_collect_accepts_sub_platform_variants() -> None:
    """Sub-platform suffixes (``"1A"``, ``"2B"``, ``"01"``) are accepted."""

    departures = [
        _dep(name="S 1", direction="Wien Floridsdorf", track="1A"),
        _dep(name="S 2", direction="Mödling", track="2B"),
        _dep(name="S 3", direction="Wien Meidling", track="01"),
    ]
    by_direction, diag = script._collect_hbf_observations(departures)
    total = sum(len(v) for v in by_direction.values())
    assert total == 3
    assert diag.dropped_non_stammstrecke_track == 0


# ---- _query_departure_board ----------------------------------------------


def test_query_departure_board_parses_modern_flat_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Response with top-level ``Departure`` list is parsed correctly."""

    captured_url: dict[str, str] = {}

    class _FakeResponse:
        status_code = 200
        content = b'{"Departure": [{"name": "S 1"}, {"name": "S 2"}]}'

    def fake_request_safe(*args: Any, **kwargs: Any) -> _FakeResponse:
        captured_url["endpoint"] = args[1] if len(args) >= 2 else kwargs.get("url", "")
        return _FakeResponse()

    monkeypatch.setattr(hbf_board, "request_safe", fake_request_safe)
    monkeypatch.setattr(script, "_charge_one_request", lambda when: None)

    when = datetime(2026, 5, 15, 8, 0, tzinfo=VIENNA_TZ)
    result = script._query_departure_board(session=cast(requests.Session, object()), when=when)
    assert len(result) == 2
    assert result[0]["name"] == "S 1"
    assert "departureBoard" in captured_url["endpoint"]


def test_query_departure_board_parses_nested_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Response with ``DepartureBoard`` wrapper is parsed correctly."""

    class _FakeResponse:
        status_code = 200
        content = (
            b'{"DepartureBoard": {"Departure": '
            b'[{"name": "REX 3"}, {"name": "S 1"}]}}'
        )

    monkeypatch.setattr(
        hbf_board,
        "request_safe",
        lambda *args, **kwargs: _FakeResponse(),
    )
    monkeypatch.setattr(script, "_charge_one_request", lambda when: None)

    when = datetime(2026, 5, 15, 8, 0, tzinfo=VIENNA_TZ)
    result = script._query_departure_board(session=cast(requests.Session, object()), when=when)
    assert len(result) == 2
    assert result[0]["name"] == "REX 3"


def test_query_departure_board_handles_single_departure_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Single-element response serialised as bare object is normalised."""

    class _FakeResponse:
        status_code = 200
        content = b'{"Departure": {"name": "S 1"}}'

    monkeypatch.setattr(
        hbf_board,
        "request_safe",
        lambda *args, **kwargs: _FakeResponse(),
    )
    monkeypatch.setattr(script, "_charge_one_request", lambda when: None)

    when = datetime(2026, 5, 15, 8, 0, tzinfo=VIENNA_TZ)
    result = script._query_departure_board(session=cast(requests.Session, object()), when=when)
    assert result == [{"name": "S 1"}]


def test_query_departure_board_returns_empty_when_field_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Response without ``Departure`` field yields an empty list (no error)."""

    class _FakeResponse:
        status_code = 200
        content = b'{}'

    monkeypatch.setattr(
        hbf_board,
        "request_safe",
        lambda *args, **kwargs: _FakeResponse(),
    )
    monkeypatch.setattr(script, "_charge_one_request", lambda when: None)

    when = datetime(2026, 5, 15, 8, 0, tzinfo=VIENNA_TZ)
    result = script._query_departure_board(session=cast(requests.Session, object()), when=when)
    assert result == []


def test_query_departure_board_raises_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """4xx/5xx responses produce a :class:`requests.HTTPError`."""

    import requests

    class _FakeResponse:
        status_code = 500
        content = b'{"errorCode": "API_GEN"}'

    monkeypatch.setattr(
        hbf_board,
        "request_safe",
        lambda *args, **kwargs: _FakeResponse(),
    )
    monkeypatch.setattr(script, "_charge_one_request", lambda when: None)

    when = datetime(2026, 5, 15, 8, 0, tzinfo=VIENNA_TZ)
    with pytest.raises(requests.HTTPError):
        script._query_departure_board(session=cast(requests.Session, object()), when=when)


def test_query_departure_board_raises_on_unparseable_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed JSON body raises :class:`ValueError` (not RecursionError)."""

    class _FakeResponse:
        status_code = 200
        content = b'{ not valid json'

    monkeypatch.setattr(
        hbf_board,
        "request_safe",
        lambda *args, **kwargs: _FakeResponse(),
    )
    monkeypatch.setattr(script, "_charge_one_request", lambda when: None)

    when = datetime(2026, 5, 15, 8, 0, tzinfo=VIENNA_TZ)
    with pytest.raises(ValueError):
        script._query_departure_board(session=cast(requests.Session, object()), when=when)


# ---- Sanity / pinning -----------------------------------------------------


def test_hauptbahnhof_id_matches_stations_directory() -> None:
    """The pinned Hbf VOR ID matches what's in ``data/stations.json``.

    Drift between this constant and the directory entry would mean the
    script polls a stale ID — this test catches a future directory
    rename or ID rewrite.
    """

    import json

    stations_path = REPO_ROOT / "data" / "stations.json"
    if not stations_path.exists():  # pragma: no cover - fresh clone
        pytest.skip("stations.json not present in this checkout")
    with stations_path.open(encoding="utf-8") as fh:
        directory = json.load(fh)
    stations = directory.get("stations", directory) if isinstance(directory, dict) else directory

    hits = [s for s in stations if s.get("name") == "Wien Hauptbahnhof"]
    assert hits, "Wien Hauptbahnhof missing from stations.json"
    assert hits[0].get("vor_id") == script.HAUPTBAHNHOF_VOR_ID


def test_direction_labels_match_csv_convention() -> None:
    """The direction labels match the CSV column values.

    The README dashboard, feed event renderer, and any external analysis
    key on "Meidling" / "Praterstern" as the direction column values
    since the 2026-05-15 rename (the legacy ``Floridsdorf`` value is
    accepted as an alias by the feed renderer's DIRECTIONS_BY_LABEL
    lookup but never produced by the canonical write path). Drifting
    these constants would silently break those consumers.
    """

    assert script.DIRECTION_LABEL_SOUTHBOUND == "Meidling"
    assert script.DIRECTION_LABEL_NORTHBOUND == "Praterstern"
    assert script.LEGACY_DIRECTION_LABEL_NORTHBOUND == "Floridsdorf"
    assert set(script.DIRECTION_LABELS) == {"Meidling", "Praterstern"}


def test_duration_window_covers_cron_interval_with_overlap() -> None:
    """Duration must exceed the 30-min cron interval for latest-wins overlap.

    Without overlap, a train scheduled near the end of one tick's window
    cannot be re-observed by the next tick (its scheduled time is in
    the past relative to the next query's ``time`` parameter), so the
    latest-wins re-observation breaks down.
    """

    assert script.DEPARTURE_BOARD_DURATION_MIN > 30


def test_process_tick_quota_exhausted_does_not_trip_breaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Quota exhaustion is a LOCAL budget signal: it must not count toward the
    circuit breaker's failure threshold. Pre-fix the charge ran inside the
    breaker-wrapped ``_query_departure_board``, so ~10 quota-blocked ticks
    OPENed the breaker for an hour and skipped ticks after the midnight reset."""

    def raising_charge(_when: datetime) -> None:
        # ``_QuotaExceeded`` is defined in the status module and imported by the
        # Hbf script; reference it via the status alias so it is mypy-visible
        # (same class object the Hbf ``except _QuotaExceeded`` catches).
        raise _legacy_script._QuotaExceeded("daily limit reached")

    fetched = {"called": False}

    def fake_query(*args: Any, **kwargs: Any) -> list[Any]:
        fetched["called"] = True
        return []

    monkeypatch.setattr(script, "_charge_one_request", raising_charge)
    monkeypatch.setattr(script, "_query_departure_board", fake_query)
    script._BREAKER.reset()
    failures_before = script._BREAKER.consecutive_failures

    when = datetime(2026, 5, 15, 8, 0, tzinfo=VIENNA_TZ)
    state: dict[Any, Any] = {}
    result = script._process_tick(session=cast(requests.Session, object()), state=state, when=when)

    assert result == "quota_exceeded"
    assert fetched["called"] is False
    assert script._BREAKER.consecutive_failures == failures_before
