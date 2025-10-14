import pytest
import requests
import responses
from responses import matchers
from datetime import datetime
from zoneinfo import ZoneInfo
from types import SimpleNamespace

import src.providers.vor as vor


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://example.test/custom", "https://example.test/custom/"),
        ("https://example.test/custom/", "https://example.test/custom/"),
        (
            "https://example.test/custom?foo=bar",
            "https://example.test/custom/?foo=bar",
        ),
        ("  https://example.test/custom  ", "https://example.test/custom/"),
        ("", ""),
    ],
)
def test_normalize_base_url_handles_edge_cases(raw, expected):
    assert vor._normalize_base_url(raw) == expected


@responses.activate
def test_location_name_contains_stoplocation():
    url = f"{vor.VOR_BASE_URL}location.name"
    payload = {"StopLocation": [{"id": "1", "name": "Wien"}]}
    responses.add(responses.GET, url, json=payload, status=200)

    resp = requests.get(url)
    data = resp.json()

    assert isinstance(data.get("StopLocation"), list)
    assert len(data["StopLocation"]) >= 1


@responses.activate
def test_resolve_station_ids_looks_up_stop_ids(monkeypatch):
    monkeypatch.setattr(vor, "VOR_ACCESS_ID", "token")
    url = f"{vor.VOR_BASE_URL}location.name"
    responses.add(
        responses.GET,
        url,
        json={"StopLocation": [{"id": "42", "name": "Wien Franz-Josefs-Bf"}]},
        status=200,
        match=[
            matchers.query_param_matcher(
                {
                    "format": "json",
                    "input": "Wien Franz-Josefs-Bf",
                    "type": "stop",
                    "accessId": "token",
                }
            )
        ],
    )

    ids = vor.resolve_station_ids(
        ["Wien Franz Josefs Bahnhof", " Wien Franz-Josefs-Bf "]
    )

    assert ids == ["42"]
    assert len(responses.calls) == 1


def test_fetch_events_prefers_configured_station_ids(monkeypatch):
    monkeypatch.setattr(vor, "VOR_ACCESS_ID", "token")
    monkeypatch.setattr(vor, "VOR_STATION_IDS", ["override"])
    monkeypatch.setattr(vor, "VOR_STATION_NAMES", ["Wien"])

    called: list[list[str]] = []

    def fail_if_called(names):
        called.append(names)
        return []

    monkeypatch.setattr(vor, "resolve_station_ids", fail_if_called)
    monkeypatch.setattr(vor, "_select_stations_round_robin", lambda ids, chunk, period: ids[:chunk])
    monkeypatch.setattr(vor, "_fetch_stationboard", lambda sid, now_local: {})
    monkeypatch.setattr(vor, "_collect_from_board", lambda sid, root: [])

    items = vor.fetch_events()

    assert items == []
    assert called == []


def test_fetch_events_uses_station_names_when_ids_missing(monkeypatch):
    monkeypatch.setattr(vor, "VOR_ACCESS_ID", "token")
    monkeypatch.setattr(vor, "VOR_STATION_IDS", [])
    monkeypatch.setattr(vor, "VOR_STATION_NAMES", ["Wien"])

    calls: list[list[str]] = []

    def fake_resolver(names: list[str]) -> list[str]:
        calls.append(names)
        return ["123"]

    monkeypatch.setattr(vor, "resolve_station_ids", fake_resolver)
    monkeypatch.setattr(vor, "_select_stations_round_robin", lambda ids, chunk, period: ids[:chunk])
    monkeypatch.setattr(vor, "_fetch_stationboard", lambda sid, now_local: {})
    monkeypatch.setattr(vor, "_collect_from_board", lambda sid, root: [])

    items = vor.fetch_events()

    assert items == []
    assert calls == [["Wien"]]


def test_collect_from_board_canonicalizes_stop_names():
    payload = {
        "Messages": {
            "Message": [
                {
                    "id": "1",
                    "act": "true",
                    "head": "Test",
                    "text": "Test text",
                    "sDate": "2024-01-01",
                    "sTime": "08:15",
                    "products": {
                        "Product": [
                            {"catOutS": "S", "name": "S1"},
                        ]
                    },
                    "affectedStops": {
                        "Stop": [
                            {"name": "Wien Franz Josefs Bahnhof"},
                            {"name": "Wien Franz-Josefs-Bf"},
                        ]
                    },
                }
            ]
        }
    }

    items = vor._collect_from_board("123", payload)

    assert items
    description = items[0]["description"]
    assert "Wien Franz-Josefs-Bf" in description
    assert "Franz Josefs Bahnhof" not in description


@pytest.mark.parametrize(
    "base_url",
    [
        "https://example.test/custom/",
        "https://example.test/custom",
    ],
)
def test_stationboard_uses_configured_base_url_and_access_id(monkeypatch, base_url):
    monkeypatch.setattr(vor, "VOR_BASE_URL", base_url)
    monkeypatch.setattr(vor, "VOR_ACCESS_ID", "token")
    monkeypatch.setattr(vor, "save_request_count", lambda now: 0)

    captured: dict[str, object] = {}

    class DummySession:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}
            self.calls: list[dict[str, object]] = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url, params=None, timeout=None):
            call = {"url": url, "params": params, "timeout": timeout}
            self.calls.append(call)
            captured.update(call)
            return SimpleNamespace(
                status_code=200,
                headers={},
                json=lambda: {"DepartureBoard": {}},
            )

    dummy_session = DummySession()

    def fake_session_with_retries(user_agent, **retry_options):
        assert user_agent == vor.VOR_USER_AGENT
        assert retry_options == vor.VOR_RETRY_OPTIONS
        return dummy_session

    monkeypatch.setattr(vor, "session_with_retries", fake_session_with_retries)

    now = datetime(2024, 1, 1, 8, 30, tzinfo=ZoneInfo("Europe/Vienna"))

    payload = vor._fetch_stationboard("123", now)

    assert payload == {"DepartureBoard": {}}
    assert dummy_session.calls
    assert dummy_session.headers["Accept"] == "application/json"

    params = captured["params"]
    assert isinstance(params, dict)
    assert captured["url"] == "https://example.test/custom/DepartureBoard"
    assert captured["timeout"] == vor.HTTP_TIMEOUT
    assert params["accessId"] == "token"
    assert params["format"] == "json"
    assert params["id"] == "123"
    assert params["duration"] == str(vor.BOARD_DURATION_MIN)
    assert params["rtMode"] == "SERVER_DEFAULT"
    assert params["date"] == now.strftime("%Y-%m-%d")
    assert params["time"] == now.strftime("%H:%M")
    assert params["requestId"].startswith("sb-123-")
