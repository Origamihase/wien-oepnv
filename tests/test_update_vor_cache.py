"""Tests for the VOR cache update script."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from scripts import update_vor_cache
from requests.exceptions import RequestException


@pytest.fixture(autouse=True)
def _mock_write_status(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Ensure no test in this module writes a real status file to the repo."""

    write_status_mock = Mock()
    monkeypatch.setattr(update_vor_cache, "write_status", write_status_mock)
    return write_status_mock


def test_cache_written_when_limit_reached(
    monkeypatch: pytest.MonkeyPatch, _mock_write_status: Mock
) -> None:
    """Ensure the cache is written even if the final request hits the limit."""

    now = datetime(2024, 1, 1, 12, tzinfo=ZoneInfo("Europe/Vienna"))
    remaining_state = {"count": update_vor_cache.MAX_REQUESTS_PER_DAY - 1}

    # Mock safety check dependencies to ensure it passes
    monkeypatch.setattr(update_vor_cache, "get_configured_stations", lambda: ["1", "2"])
    monkeypatch.setattr(update_vor_cache, "select_stations_for_run", lambda stations: stations)

    monkeypatch.setattr(update_vor_cache, "_now_local", lambda: now)
    monkeypatch.setattr(
        update_vor_cache,
        "_todays_request_count",
        lambda current: remaining_state["count"],
    )

    calls: list[int] = []

    def fake_fetch_events(*args: object, **kwargs: object) -> list[dict[str, str]]:
        remaining_state["count"] += 1
        calls.append(remaining_state["count"])
        return [{"id": "event"}]

    monkeypatch.setattr(update_vor_cache, "fetch_events", fake_fetch_events)

    write_cache_mock = Mock()
    monkeypatch.setattr(update_vor_cache, "write_cache", write_cache_mock)
    monkeypatch.setattr(update_vor_cache, "serialize_for_cache", lambda item: item)

    save_request_count_mock = Mock()
    monkeypatch.setattr(
        update_vor_cache,
        "save_request_count",
        save_request_count_mock,
        raising=False,
    )

    exit_code = update_vor_cache.main()

    assert exit_code == 0
    write_cache_mock.assert_called_once_with("vor", [{"id": "event"}])
    assert calls == [update_vor_cache.MAX_REQUESTS_PER_DAY]
    assert remaining_state["count"] == update_vor_cache.MAX_REQUESTS_PER_DAY
    save_request_count_mock.assert_not_called()


def test_main_returns_success_when_fetch_fails(
    monkeypatch: pytest.MonkeyPatch, _mock_write_status: Mock
) -> None:
    """Network failures must not cause a non-zero exit status."""

    # Mock safety check dependencies to ensure it passes
    monkeypatch.setattr(update_vor_cache, "get_configured_stations", lambda: ["1", "2"])
    monkeypatch.setattr(update_vor_cache, "select_stations_for_run", lambda stations: stations)

    monkeypatch.setattr(update_vor_cache, "_limit_reached", lambda now: False)
    monkeypatch.setattr(
        update_vor_cache,
        "fetch_events",
        lambda *args, **kwargs: (_ for _ in ()).throw(RequestException("boom")),
    )

    exit_code = update_vor_cache.main()

    assert exit_code == 0
    assert _mock_write_status.call_count == 1
    provider_arg, payload = _mock_write_status.call_args.args
    assert provider_arg == "vor"
    assert payload["status"] == "api_unreachable"


def test_cache_written_when_empty_list_returned(
    monkeypatch: pytest.MonkeyPatch, _mock_write_status: Mock
) -> None:
    """Ensure an empty list is cached and returns success."""

    # Mock safety check dependencies to ensure it passes
    monkeypatch.setattr(update_vor_cache, "get_configured_stations", lambda: ["1", "2"])
    monkeypatch.setattr(update_vor_cache, "select_stations_for_run", lambda stations: stations)

    monkeypatch.setattr(update_vor_cache, "_limit_reached", lambda now: False)

    # Return empty list
    monkeypatch.setattr(update_vor_cache, "fetch_events", lambda *args, **kwargs: [])

    write_cache_mock = Mock()
    monkeypatch.setattr(update_vor_cache, "write_cache", write_cache_mock)
    monkeypatch.setattr(update_vor_cache, "serialize_for_cache", lambda item: item)

    exit_code = update_vor_cache.main()

    assert exit_code == 0
    write_cache_mock.assert_called_once_with("vor", [])


def test_status_marker_records_ok_run(
    monkeypatch: pytest.MonkeyPatch, _mock_write_status: Mock
) -> None:
    """A successful run must leave a heartbeat with status=ok and event count."""

    monkeypatch.setattr(update_vor_cache, "get_configured_stations", lambda: ["1", "2"])
    monkeypatch.setattr(update_vor_cache, "select_stations_for_run", lambda stations: stations)
    monkeypatch.setattr(update_vor_cache, "_limit_reached", lambda now: False)
    monkeypatch.setattr(update_vor_cache, "_todays_request_count", lambda now: 14)
    monkeypatch.setattr(
        update_vor_cache, "fetch_events", lambda *args, **kwargs: [{"id": "x"}]
    )
    monkeypatch.setattr(update_vor_cache, "write_cache", Mock())
    monkeypatch.setattr(update_vor_cache, "serialize_for_cache", lambda item: item)

    exit_code = update_vor_cache.main()

    assert exit_code == 0
    assert _mock_write_status.call_count == 1
    provider_arg, payload = _mock_write_status.call_args.args
    assert provider_arg == "vor"
    assert payload["status"] == "ok"
    assert payload["events_collected"] == 1
    assert payload["stations_queried"] == 2
    assert payload["requests_used_today"] == 14
    assert payload["daily_limit"] == update_vor_cache.MAX_REQUESTS_PER_DAY
    assert "last_run_at" in payload
    assert "last_run_at_local" in payload


def test_status_marker_records_quota_skip(
    monkeypatch: pytest.MonkeyPatch, _mock_write_status: Mock
) -> None:
    """When the daily quota is exhausted the heartbeat must reflect the skip."""

    now = datetime(2024, 1, 1, 12, tzinfo=ZoneInfo("Europe/Vienna"))
    monkeypatch.setattr(update_vor_cache, "get_configured_stations", lambda: ["1", "2"])
    monkeypatch.setattr(update_vor_cache, "select_stations_for_run", lambda stations: stations)
    monkeypatch.setattr(update_vor_cache, "_now_local", lambda: now)
    monkeypatch.setattr(
        update_vor_cache,
        "_todays_request_count",
        lambda _now: update_vor_cache.MAX_REQUESTS_PER_DAY,
    )

    fetch_mock = Mock()
    monkeypatch.setattr(update_vor_cache, "fetch_events", fetch_mock)

    exit_code = update_vor_cache.main()

    assert exit_code == 0
    fetch_mock.assert_not_called()
    assert _mock_write_status.call_count == 1
    _provider, payload = _mock_write_status.call_args.args
    assert payload["status"] == "skipped_quota"
    assert "events_collected" not in payload
