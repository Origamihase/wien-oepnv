import os
import json
from unittest.mock import MagicMock
import pytest
import src.providers.vor as vor

# --- Test Case ---

def test_fetch_events_uses_whitelist_by_default(monkeypatch, caplog):
    """
    Verify that fetch_events uses the default whitelist (Hbf, Airport)
    when no env var is set, and resolves their IDs properly.
    """
    # Mock credentials and dependencies
    monkeypatch.setattr(vor, "refresh_access_credentials", lambda: "test")
    monkeypatch.setattr(vor, "VOR_ACCESS_ID", "test", raising=False)

    # Mock limits to allow execution
    monkeypatch.setattr(vor, "MAX_REQUESTS_PER_DAY", 100)
    monkeypatch.setattr(vor, "load_request_count", lambda: (None, 0))
    monkeypatch.setattr(vor, "save_request_count", lambda dt: 1)

    # Mock VOR_STATION_IDS to ensure we don't fall back to it or use it if whitelist is active
    monkeypatch.setattr(vor, "VOR_STATION_IDS", ["99999", "88888"])

    # Mock resolve_station_ids to track what's being resolved
    resolved_ids = []
    def mock_resolve(names):
        nonlocal resolved_ids
        resolved_ids.extend(names)
        return ["490134900", "430470800"] # Mock IDs for Hbf and Airport

    monkeypatch.setattr(vor, "resolve_station_ids", mock_resolve)

    # Mock fetching to avoid network
    captured_ids = []
    def mock_fetch(station_id, now_local):
        captured_ids.append(station_id)
        return {} # Return empty dict to simulate success

    monkeypatch.setattr(vor, "_fetch_traffic_info", mock_fetch)

    # Ensure env var is NOT set (simulating default)
    monkeypatch.delenv("VOR_MONITOR_STATIONS_WHITELIST", raising=False)

    with caplog.at_level("INFO"):
        vor.fetch_events()

    # Verify that we resolved the default whitelist names
    assert "Wien Hauptbahnhof" in resolved_ids
    assert "Flughafen Wien" in resolved_ids

    # Verify that we tried to fetch the corresponding IDs
    assert "490134900" in captured_ids
    assert "430470800" in captured_ids

    # Verify we did NOT use the fallback IDs
    assert "99999" not in captured_ids


def test_fetch_events_uses_configured_whitelist(monkeypatch):
    """
    Verify that setting VOR_MONITOR_STATIONS_WHITELIST overrides default.
    """
    monkeypatch.setattr(vor, "refresh_access_credentials", lambda: "test")
    monkeypatch.setattr(vor, "VOR_ACCESS_ID", "test", raising=False)
    monkeypatch.setattr(vor, "load_request_count", lambda: (None, 0))
    monkeypatch.setattr(vor, "save_request_count", lambda dt: 1)

    monkeypatch.setenv("VOR_MONITOR_STATIONS_WHITELIST", "Westbahnhof, Meidling")

    resolved_names = []
    def mock_resolve(names):
        resolved_names.extend(names)
        return ["111", "222"]

    monkeypatch.setattr(vor, "resolve_station_ids", mock_resolve)

    fetched_ids = []
    def mock_fetch(sid, now):
        fetched_ids.append(sid)
        return {}
    monkeypatch.setattr(vor, "_fetch_traffic_info", mock_fetch)

    vor.fetch_events()

    assert "Westbahnhof" in resolved_names
    assert "Meidling" in resolved_names
    assert "Wien Hauptbahnhof" not in resolved_names


def test_fetch_events_disabled_whitelist_fallback(monkeypatch):
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

    fetched_ids = []
    def mock_fetch(sid, now):
        fetched_ids.append(sid)
        return {}
    monkeypatch.setattr(vor, "_fetch_traffic_info", mock_fetch)

    # Mock resolve_station_ids to assert it's NOT called for whitelist
    def mock_resolve(names):
        assert False, "Should not be resolving names when whitelist is empty"
    monkeypatch.setattr(vor, "resolve_station_ids", mock_resolve)

    vor.fetch_events()

    assert "12345" in fetched_ids


def test_whitelist_respects_request_limits(monkeypatch, caplog):
    """
    Verify request limits apply even when using whitelist.
    """
    monkeypatch.setattr(vor, "refresh_access_credentials", lambda: "test")
    monkeypatch.setattr(vor, "VOR_ACCESS_ID", "test", raising=False)
    monkeypatch.setattr(vor, "MAX_REQUESTS_PER_DAY", 100)

    # Simulate limit reached
    monkeypatch.setattr(vor, "load_request_count", lambda: (None, 100))

    captured_ids = []
    def mock_fetch(sid, now):
        captured_ids.append(sid)
        return {}
    monkeypatch.setattr(vor, "_fetch_traffic_info", mock_fetch)

    with caplog.at_level("INFO"):
        items = vor.fetch_events()

    assert items == []
    assert len(captured_ids) == 0
    assert any("Tageslimit" in r.getMessage() for r in caplog.records)
