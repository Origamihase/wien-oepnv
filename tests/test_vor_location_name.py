import requests
import responses
from responses import matchers

import src.providers.vor as vor


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
    monkeypatch.setattr(vor, "refresh_access_credentials", lambda: "token")
    monkeypatch.setattr(vor, "VOR_ACCESS_ID", "token", raising=False)

    # We must patch fetch_content_safe because it tries to verify IP/socket
    # which is not available with 'responses' mocking.
    # We return the JSON bytes directly.
    import json

    def fake_fetch_safe(session, url, params=None, timeout=None):
        # Verify params
        assert params["input"] == "Wien Franz-Josefs-Bf"
        # accessId is injected via apply_authentication, which modifies the request or session,
        # but here fetch_content_safe receives params directly.
        # In the original code:
        # with session_with_retries(...) as session:
        #    apply_authentication(session)  <-- this patches session.request or session.get
        #    ...
        #    fetch_content_safe(session, url, params=params, ...)
        #
        # fetch_content_safe calls session.get(url, params=params).
        # Since apply_authentication wraps session.get/request, the 'accessId' is injected THERE,
        # not into the 'params' dict passed to fetch_content_safe.
        # So 'params' here won't have 'accessId'.

        return json.dumps({
            "StopLocation": [{"id": "42", "name": "Wien Franz-Josefs-Bf"}]
        }).encode("utf-8")

    monkeypatch.setattr(vor, "fetch_content_safe", fake_fetch_safe)

    ids = vor.resolve_station_ids(
        ["Wien Franz Josefs Bahnhof", " Wien Franz-Josefs-Bf "]
    )

    assert ids == ["42"]


def test_fetch_events_prefers_configured_station_ids(monkeypatch):
    monkeypatch.setattr(vor, "refresh_access_credentials", lambda: "token")
    monkeypatch.setattr(vor, "VOR_ACCESS_ID", "token", raising=False)
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
    monkeypatch.setattr(vor, "refresh_access_credentials", lambda: "token")
    monkeypatch.setattr(vor, "VOR_ACCESS_ID", "token", raising=False)
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
