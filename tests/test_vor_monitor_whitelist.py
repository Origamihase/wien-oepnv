from typing import Any

import pytest
import src.providers.vor as vor

# --- Test Case ---

def test_fetch_events_default_whitelist_is_empty(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    Verify that the default ``DEFAULT_MONITOR_WHITELIST`` is empty as of
    the 2026-05-09 VOR-quota optimization. Without the env override the
    departure-board polling MUST NOT make any API calls — the budget
    is reserved for the Stammstrecke ``/trip`` polling.

    ``VOR_STATION_IDS`` continues to provide the legacy explicit-IDs
    fallback path; this test asserts the *default* (empty whitelist,
    empty IDs) produces zero network activity.
    """
    monkeypatch.setattr(vor, "refresh_access_credentials", lambda: "test")
    monkeypatch.setattr(vor, "VOR_ACCESS_ID", "test", raising=False)
    monkeypatch.setattr(vor, "MAX_REQUESTS_PER_DAY", 100)
    monkeypatch.setattr(vor, "load_request_count", lambda: (None, 0))
    monkeypatch.setattr(vor, "save_request_count", lambda dt: 1)

    # No legacy fallback IDs configured.
    monkeypatch.setattr(vor, "VOR_STATION_IDS", [])

    # ``resolve_station_ids`` must NOT be called when the whitelist is
    # empty — there are no names to resolve, and a stray call would
    # consume the VAO Start budget the migration is trying to preserve.
    def mock_resolve(names: list[str]) -> list[str]:
        raise AssertionError(
            f"resolve_station_ids called with default empty whitelist: {names!r}"
        )

    monkeypatch.setattr(vor, "resolve_station_ids", mock_resolve)

    captured_ids: list[str] = []
    def mock_fetch(station_id: str, now_local: Any, counter: Any = None, session: Any = None, timeout: Any = None) -> dict[str, Any]:
        captured_ids.append(station_id)
        return {}

    monkeypatch.setattr(vor, "_fetch_departure_board_for_station", mock_fetch)

    monkeypatch.delenv("VOR_MONITOR_STATIONS_WHITELIST", raising=False)

    # Pin the constant — a regression that re-introduces a default
    # whitelist would silently re-enable departure-board polling.
    assert vor.DEFAULT_MONITOR_WHITELIST == ""

    with caplog.at_level("INFO"):
        vor.fetch_events()

    # Default empty whitelist + empty VOR_STATION_IDS → no network call.
    assert captured_ids == []


def test_fetch_events_uses_configured_whitelist(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Verify that setting VOR_MONITOR_STATIONS_WHITELIST overrides default.
    """
    monkeypatch.setattr(vor, "refresh_access_credentials", lambda: "test")
    monkeypatch.setattr(vor, "VOR_ACCESS_ID", "test", raising=False)
    monkeypatch.setattr(vor, "load_request_count", lambda: (None, 0))
    monkeypatch.setattr(vor, "save_request_count", lambda dt: 1)

    monkeypatch.setenv("VOR_MONITOR_STATIONS_WHITELIST", "Westbahnhof, Meidling")

    resolved_names: list[str] = []
    def mock_resolve(names: list[str]) -> list[str]:
        resolved_names.extend(names)
        return ["111", "222"]

    monkeypatch.setattr(vor, "resolve_station_ids", mock_resolve)

    fetched_ids: list[str] = []
    def mock_fetch(sid: str, now: Any, counter: Any = None, session: Any = None, timeout: Any = None) -> dict[str, Any]:
        fetched_ids.append(sid)
        return {}
    monkeypatch.setattr(vor, "_fetch_departure_board_for_station", mock_fetch)

    vor.fetch_events()

    assert "Westbahnhof" in resolved_names
    assert "Meidling" in resolved_names
    assert "Wien Hauptbahnhof" not in resolved_names


def test_fetch_events_disabled_whitelist_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Verify that setting VOR_MONITOR_STATIONS_WHITELIST to empty string
    disables the whitelist and falls back to VOR_STATION_IDS.
    """
    monkeypatch.setattr(vor, "refresh_access_credentials", lambda: "test")
    monkeypatch.setattr(vor, "VOR_ACCESS_ID", "test", raising=False)
    monkeypatch.setattr(vor, "load_request_count", lambda: (None, 0))
    monkeypatch.setattr(vor, "save_request_count", lambda dt: 1)

    monkeypatch.setenv("VOR_MONITOR_STATIONS_WHITELIST", "") # Explicitly empty

    # Setup legacy VOR_STATION_IDS
    monkeypatch.setattr(vor, "VOR_STATION_IDS", ["12345"])

    fetched_ids: list[str] = []
    def mock_fetch(sid: str, now: Any, counter: Any = None, session: Any = None, timeout: Any = None) -> dict[str, Any]:
        fetched_ids.append(sid)
        return {}
    monkeypatch.setattr(vor, "_fetch_departure_board_for_station", mock_fetch)

    # Mock resolve_station_ids to assert it's NOT called for whitelist
    def mock_resolve(names: list[str]) -> None:
        assert False, "Should not be resolving names when whitelist is empty"
    monkeypatch.setattr(vor, "resolve_station_ids", mock_resolve)

    vor.fetch_events()

    assert "12345" in fetched_ids


def test_whitelist_respects_request_limits(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    Verify request limits apply even when using whitelist.
    """
    monkeypatch.setattr(vor, "refresh_access_credentials", lambda: "test")
    monkeypatch.setattr(vor, "VOR_ACCESS_ID", "test", raising=False)
    monkeypatch.setattr(vor, "MAX_REQUESTS_PER_DAY", 100)

    # Simulate limit reached
    monkeypatch.setattr(vor, "load_request_count", lambda: (None, 100))

    captured_ids: list[str] = []
    def mock_fetch(sid: str, now: Any, counter: Any = None, session: Any = None, timeout: Any = None) -> dict[str, Any]:
        captured_ids.append(sid)
        return {}
    monkeypatch.setattr(vor, "_fetch_departure_board_for_station", mock_fetch)

    from requests import RequestException
    import pytest

    with caplog.at_level("INFO"):
        with pytest.raises(RequestException) as excinfo:
            vor.fetch_events()

    assert "Tageslimit" in str(excinfo.value)
    assert len(captured_ids) == 0
