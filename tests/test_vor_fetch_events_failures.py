from datetime import datetime

import pytest

import src.providers.vor as vor


def _today_vienna_iso() -> str:
    return datetime.now(vor.ZoneInfo("Europe/Vienna")).date().isoformat()


@pytest.fixture(autouse=True)
def _reset_station_ids(monkeypatch):
    monkeypatch.setattr(vor, "refresh_access_credentials", lambda: "token")
    monkeypatch.setattr(vor, "VOR_ACCESS_ID", "token", raising=False)
    # Clear the whitelist to force usage of VOR_STATION_IDS
    monkeypatch.setenv("VOR_MONITOR_STATIONS_WHITELIST", "")
    monkeypatch.setattr(vor, "VOR_STATION_IDS", ["430470800", "490091000"])
    monkeypatch.setattr(vor, "MAX_STATIONS_PER_RUN", 2)
    monkeypatch.setattr(vor, "ROTATION_INTERVAL_SEC", 60)
    monkeypatch.setattr(vor, "MAX_REQUESTS_PER_DAY", 1000)
    today = _today_vienna_iso()
    monkeypatch.setattr(vor, "load_request_count", lambda: (today, 0))


def test_fetch_events_raises_when_all_stationboards_fail(monkeypatch):
    monkeypatch.setattr(vor, "_fetch_departure_board_for_station", lambda sid, now: None)
    with pytest.raises(vor.RequestException):
        vor.fetch_events()


def test_fetch_events_returns_results_when_some_stationboards_succeed(monkeypatch):
    payloads = {"430470800": object(), "490091000": None}

    def fake_fetch(station_id: str, now):
        return payloads.get(station_id)

    def fake_collect(station_id: str, payload):
        return [
            {
                "guid": f"guid-{station_id}",
                "source": "VOR/VAO",
                "category": "Störung",
                "title": "Test",
                "description": "Test",
                "link": "https://www.vor.at/",
                "pubDate": None,
                "starts_at": None,
                "ends_at": None,
            }
        ]

    monkeypatch.setattr(vor, "_fetch_departure_board_for_station", fake_fetch)
    monkeypatch.setattr(vor, "_collect_from_board", fake_collect)

    items = vor.fetch_events()
    assert items
    assert {item["guid"] for item in items} == {"guid-430470800"}
