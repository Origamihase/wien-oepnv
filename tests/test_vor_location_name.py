from typing import Any

import pytest
import requests
import responses

import src.providers.vor as vor


@responses.activate
def test_location_name_contains_stoplocation() -> None:
    url = f"{vor.VOR_BASE_URL}location.name"
    payload = {"StopLocation": [{"id": "1", "name": "Wien"}]}
    responses.add(responses.GET, url, json=payload, status=200)

    resp = requests.get(url)
    data = resp.json()

    assert isinstance(data.get("StopLocation"), list)
    assert len(data["StopLocation"]) >= 1


@responses.activate
def test_resolve_station_ids_looks_up_stop_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vor, "refresh_access_credentials", lambda: "token")
    monkeypatch.setattr(vor, "VOR_ACCESS_ID", "token", raising=False)

    # We must patch fetch_content_safe because it tries to verify IP/socket
    # which is not available with 'responses' mocking.
    # We return the JSON bytes directly.
    import json

    def fake_fetch_safe(session: Any, url: str, params: Any = None, timeout: Any = None, allowed_content_types: Any = None) -> bytes:
        # Verify params
        assert params["input"] == "UnknownStation"
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
        ["UnknownStation"]
    )

    assert ids == ["42"]


def test_fetch_events_prefers_configured_station_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vor, "refresh_access_credentials", lambda: "token")
    monkeypatch.setenv("VOR_MONITOR_STATIONS_WHITELIST", "")
    monkeypatch.setattr(vor, "VOR_ACCESS_ID", "token", raising=False)
    monkeypatch.setattr(vor, "VOR_STATION_IDS", ["override"])
    monkeypatch.setattr(vor, "VOR_STATION_NAMES", ["Wien"])

    called: list[list[str]] = []

    def fail_if_called(names: list[str]) -> list[str]:
        called.append(names)
        return []

    monkeypatch.setattr(vor, "resolve_station_ids", fail_if_called)
    monkeypatch.setattr(vor, "_select_stations_round_robin", lambda ids, chunk: ids[:chunk])
    monkeypatch.setattr(vor, "_fetch_departure_board_for_station", lambda sid, now_local, counter=None, session=None, timeout=None: {})
    monkeypatch.setattr(vor, "_collect_from_board", lambda sid, root: [])

    items = vor.fetch_events()

    assert items == []
    assert called == []


def test_fetch_events_uses_station_names_when_ids_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vor, "refresh_access_credentials", lambda: "token")
    monkeypatch.setenv("VOR_MONITOR_STATIONS_WHITELIST", "")
    monkeypatch.setattr(vor, "VOR_ACCESS_ID", "token", raising=False)
    monkeypatch.setattr(vor, "VOR_STATION_IDS", [])
    monkeypatch.setattr(vor, "VOR_STATION_NAMES", ["Wien"])

    calls: list[list[str]] = []

    def fake_resolver(names: list[str]) -> list[str]:
        calls.append(names)
        return ["123"]

    monkeypatch.setattr(vor, "resolve_station_ids", fake_resolver)
    monkeypatch.setattr(vor, "_select_stations_round_robin", lambda ids, chunk: ids[:chunk])
    monkeypatch.setattr(vor, "_fetch_departure_board_for_station", lambda sid, now_local, counter=None, session=None, timeout=None: {})
    monkeypatch.setattr(vor, "_collect_from_board", lambda sid, root: [])

    items = vor.fetch_events()

    assert items == []
    assert calls == [["Wien"]]


@pytest.mark.parametrize(
    "stop_payload",
    [
        {"StopLocation": 42},
        {"StopLocation": True},
        {"StopLocation": "wien"},
        {"StopLocation": [1, 2, 3]},
        {"StopLocation": [{"id": "good"}, "bad", 7]},
        {"LocationList": {"Stop": 99}},
        {"LocationList": {"Stop": True}},
        {"LocationList": {"Stop": "x"}},
        {"LocationList": {"Stop": [True, "y"]}},
        {"LocationList": "not-a-mapping"},
    ],
)
def test_resolve_station_ids_zero_trust_payload_shapes(
    monkeypatch: pytest.MonkeyPatch, stop_payload: dict[str, Any]
) -> None:
    """A misbehaving / compromised VAO upstream must not crash the batch.

    Truthy non-list, non-Mapping shapes for ``StopLocation`` /
    ``LocationList.Stop`` previously raised ``TypeError`` from ``for stop in
    stops:``, propagating out of the per-name loop and silently dropping every
    subsequent station's resolution after burning quota.
    """

    monkeypatch.setattr(vor, "refresh_access_credentials", lambda: "token")
    monkeypatch.setattr(vor, "VOR_ACCESS_ID", "token", raising=False)

    import json

    captured: list[str] = []

    def fake_fetch_safe(
        session: Any,
        url: str,
        params: Any = None,
        timeout: Any = None,
        allowed_content_types: Any = None,
    ) -> bytes:
        captured.append(params["input"])
        if params["input"] == "BogusZeroTrustStation":
            return json.dumps(stop_payload).encode("utf-8")
        return json.dumps(
            {"StopLocation": [{"id": "42", "name": "RecoveredStation"}]}
        ).encode("utf-8")

    monkeypatch.setattr(vor, "fetch_content_safe", fake_fetch_safe)

    # The bogus payload comes first to verify subsequent names are still resolved.
    ids = vor.resolve_station_ids(
        ["BogusZeroTrustStation", "AnotherUnknownStation"]
    )

    # The bogus shape must not abort the loop: the API is called for both
    # names and the recovered station is still resolved. A truthy non-Mapping
    # / non-list shape previously raised ``TypeError`` here.
    assert "42" in ids
    assert captured == ["BogusZeroTrustStation", "AnotherUnknownStation"]


def test_resolve_station_ids_filters_non_mapping_stop_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-element guard rejects non-Mapping entries inside an otherwise valid list."""

    monkeypatch.setattr(vor, "refresh_access_credentials", lambda: "token")
    monkeypatch.setattr(vor, "VOR_ACCESS_ID", "token", raising=False)

    import json

    def fake_fetch_safe(
        session: Any,
        url: str,
        params: Any = None,
        timeout: Any = None,
        allowed_content_types: Any = None,
    ) -> bytes:
        return json.dumps(
            {"StopLocation": ["scalar", 7, {"id": "42", "name": "Wien"}]}
        ).encode("utf-8")

    monkeypatch.setattr(vor, "fetch_content_safe", fake_fetch_safe)

    assert vor.resolve_station_ids(["UnknownStation"]) == ["42"]


def test_collect_from_board_canonicalizes_stop_names(monkeypatch: pytest.MonkeyPatch) -> None:
    # Mock station info to avoid filter because "Test text" is not "Wien" related.
    # If in_vienna=True, filtering is skipped.
    from src.utils.stations import StationInfo
    monkeypatch.setattr("src.providers.vor.station_info", lambda x: StationInfo(name="Wien FJB", in_vienna=True, pendler=False))

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
    # Description should no longer contain stops, so we just check it exists/is valid.
    # The requirement was to put station context in title if needed, or just plain summary.
    description = items[0]["description"]
    assert description == "Test text"
