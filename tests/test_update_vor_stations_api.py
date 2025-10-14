"""Tests for the VOR station directory API integration."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from scripts import update_vor_stations as module


def test_parse_api_stop_with_properties() -> None:
    data = {
        "id": "900100",
        "name": "Wien Aspern Nord",
        "coord": {"lat": 48.234567, "lon": 16.520123},
        "properties": [
            {"name": "municipality", "value": "Wien"},
            {"name": "shortName", "value": "Aspern Nord"},
            {"name": "globalId", "value": "AT:900100"},
            {"name": "gtfsStopId", "value": "900100"},
        ],
    }

    stop = module._parse_api_stop(data, wanted_id="900100")

    assert stop is not None
    assert stop.vor_id == "900100"
    assert stop.name == "Wien Aspern Nord"
    assert stop.municipality == "Wien"
    assert stop.short_name == "Aspern Nord"
    assert stop.global_id == "AT:900100"
    assert stop.gtfs_stop_id == "900100"
    assert pytest.approx(stop.latitude or 0.0, rel=1e-6) == 48.234567
    assert pytest.approx(stop.longitude or 0.0, rel=1e-6) == 16.520123


@dataclass
class _FakeResponse:
    status_code: int
    payload: dict

    def json(self) -> dict:
        return self.payload


class _FakeSession:
    def __init__(self, payloads: dict[str, tuple[int, dict]]):
        self.payloads = payloads
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, *, params: dict, timeout: object, headers: dict) -> _FakeResponse:
        station_id = params["input"]
        self.calls.append((url, params))
        status, payload = self.payloads[station_id]
        return _FakeResponse(status_code=status, payload=payload)

    def __enter__(self) -> "_FakeSession":  # pragma: no cover - context management helper
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # pragma: no cover - context management helper
        return None


def test_fetch_vor_stops_from_api_uses_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    payloads = {
        "900100": (
            200,
            {
                "StopLocation": {
                    "id": "900100",
                    "name": "Wien Aspern Nord",
                    "coord": {"lat": 48.234567, "lon": 16.520123},
                    "municipality": "Wien",
                }
            },
        ),
        "900200": (500, {}),
    }

    fake_session = _FakeSession(payloads)

    monkeypatch.setattr(module, "session_with_retries", lambda *args, **kwargs: fake_session)
    monkeypatch.setattr(module.vor_provider, "refresh_access_credentials", lambda: "token")
    monkeypatch.setattr(module.vor_provider, "VOR_ACCESS_ID", "token", raising=False)

    fallback = {
        "900200": module.VORStop(
            vor_id="900200",
            name="Fallback Stop",
            latitude=None,
            longitude=None,
        )
    }

    stops = module.fetch_vor_stops_from_api(["900100", "900200"], fallback=fallback)

    assert [stop.vor_id for stop in stops] == ["900100", "900200"]
    assert any("location.name" in call[0] for call in fake_session.calls)
    assert module.vor_provider.refresh_access_credentials() == "token"

