import pytest
import threading
import time
from src.providers import vor

def test_fetch_events_graceful_shutdown_on_emergency_stop(monkeypatch):
    """
    Verify that if one thread triggers an 'Emergency Stop', the executor shuts down,
    the loop breaks, and partial results are returned (graceful degradation),
    instead of crashing the entire feed build.
    """
    # 1. Setup mocked station IDs and environment
    monkeypatch.setattr(vor, "refresh_access_credentials", lambda: "token")
    monkeypatch.setattr(vor, "VOR_ACCESS_ID", "token", raising=False)
    monkeypatch.setenv("VOR_MONITOR_STATIONS_WHITELIST", "")

    # We use 3 stations:
    # 1. Success -> returns a valid item
    # 2. Emergency Stop -> triggers the circuit breaker
    # 3. Pending -> should be cancelled or not executed (depending on timing)
    station_ids = ["SUCCESS_STATION", "EMERGENCY_STATION", "PENDING_STATION"]
    monkeypatch.setattr(vor, "VOR_STATION_IDS", station_ids)
    monkeypatch.setattr(vor, "MAX_STATIONS_PER_RUN", 3)
    monkeypatch.setattr(vor, "ROTATION_INTERVAL_SEC", 60)
    monkeypatch.setattr(vor, "MAX_REQUESTS_PER_DAY", 1000)

    # Mock load_request_count to allow execution
    monkeypatch.setattr(vor, "load_request_count", lambda: ("2023-01-01", 0))

    # 2. Mock _fetch_departure_board_for_station
    def fake_fetch(station_id, now, counter=None):
        if station_id == "SUCCESS_STATION":
            # Return a valid payload
            return {
                "departureBoard": {
                    "Message": {
                        "head": "Success",
                        "text": "All good",
                        "sDate": "2023-01-01",
                        "sTime": "12:00"
                    }
                }
            }
        elif station_id == "EMERGENCY_STATION":
            # Simulate the circuit breaker triggering
            # Sleep slightly to ensure SUCCESS_STATION has a chance to complete first
            time.sleep(0.1)
            raise RuntimeError("Emergency Stop: Too many requests in single run!")
        else:
            # PENDING_STATION
            return None

    monkeypatch.setattr(vor, "_fetch_departure_board_for_station", fake_fetch)

    def fake_collect(station_id, payload):
        return [{
            "title": "Success",
            "description": "All good",
            "guid": "123"
        }]

    monkeypatch.setattr(vor, "_collect_from_board", fake_collect)

    # 3. Run fetch_events
    # It should NOT raise RuntimeError, but return the successful item.
    items = vor.fetch_events()

    # 4. Verify results
    assert len(items) == 1
    assert items[0]["title"] == "Success"
