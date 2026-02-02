"""Tests for the VOR cache update script."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from scripts import update_vor_cache
from requests.exceptions import RequestException


def test_cache_written_when_limit_reached(monkeypatch) -> None:
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

    def fake_fetch_events(*args, **kwargs) -> list[dict[str, str]]:
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


def test_main_returns_success_when_fetch_fails(monkeypatch) -> None:
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
