"""Tests for the VOR station directory API integration."""

from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType
from typing import Any

import pytest

from scripts import update_vor_stations as module


def test_parse_api_stop_with_properties() -> None:
    data = {
        "id": "490091000",
        "name": "Wien Aspern Nord",
        "coord": {"lat": 48.234567, "lon": 16.520123},
        "properties": [
            {"name": "municipality", "value": "Wien"},
            {"name": "shortName", "value": "Aspern Nord"},
            {"name": "globalId", "value": "AT:490091000"},
            {"name": "gtfsStopId", "value": "490091000"},
        ],
    }

    stop = module._parse_api_stop(data, wanted_id="490091000")

    assert stop is not None
    assert stop.vor_id == "490091000"
    assert stop.name == "Wien Aspern Nord"
    assert stop.municipality == "Wien"
    assert stop.short_name == "Aspern Nord"
    assert stop.global_id == "AT:490091000"
    assert stop.gtfs_stop_id == "490091000"
    assert pytest.approx(stop.latitude or 0.0, rel=1e-6) == 48.234567
    assert pytest.approx(stop.longitude or 0.0, rel=1e-6) == 16.520123


@dataclass
class _FakeResponse:
    status_code: int
    payload: dict[str, Any]

    def json(self) -> dict[str, Any]:
        return self.payload


class _FakeSession:
    def __init__(self, payloads: dict[str, tuple[int, dict[str, Any]]]) -> None:
        self.payloads = payloads
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, dict[str, str]]] = []

    def request(self, method: str, url: str, **kwargs: object) -> _FakeResponse:
        from typing import cast
        params = cast(dict[str, str], kwargs.get("params") or {})
        station_id = params["input"]
        self.calls.append((url, params))
        status, payload = self.payloads[station_id]
        return _FakeResponse(status_code=status, payload=payload)

    def get(
        self,
        url: str,
        *,
        params: dict[str, str],
        timeout: object,
        headers: dict[str, str],
    ) -> _FakeResponse:
        return self.request("GET", url, params=params, timeout=timeout, headers=headers)

    def __enter__(self) -> _FakeSession:  # pragma: no cover - context management helper
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:  # pragma: no cover - context management helper
        return None


def test_fetch_vor_stops_from_api_uses_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    payloads = {
        "490091000": (
            200,
            {
                "StopLocation": {
                    "id": "490091000",
                    "name": "Wien Aspern Nord",
                    "coord": {"lat": 48.234567, "lon": 16.520123},
                    "municipality": "Wien",
                }
            },
        ),
        "430470800": (500, {}),
    }

    fake_session = _FakeSession(payloads)

    monkeypatch.setattr(module, "session_with_retries", lambda *args, **kwargs: fake_session)
    monkeypatch.setattr(module.vor_provider, "refresh_access_credentials", lambda: "token")
    monkeypatch.setattr(module.vor_provider, "VOR_ACCESS_ID", "token", raising=False)

    fallback = {
        "430470800": module.VORStop(
            vor_id="430470800",
            name="Fallback Stop",
            latitude=None,
            longitude=None,
        )
    }

    stops = module.fetch_vor_stops_from_api(["490091000", "430470800"], fallback=fallback)

    assert [stop.vor_id for stop in stops] == ["490091000", "430470800"]
    assert any("location.name" in call[0] for call in fake_session.calls)
    assert module.vor_provider.refresh_access_credentials() == "token"


@dataclass
class _FakeAnyResponse:
    """Like _FakeResponse but lets us return non-dict payloads from .json()."""

    status_code: int
    payload: Any

    def json(self) -> Any:
        return self.payload


class _FakeAnySession:
    def __init__(self, payloads: dict[str, tuple[int, Any]]) -> None:
        self.payloads = payloads
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(
        self,
        url: str,
        *,
        params: dict[str, str],
        timeout: object,
        headers: dict[str, str],
    ) -> _FakeAnyResponse:
        from typing import cast

        params_typed = cast(dict[str, str], params)
        station_id = params_typed["input"]
        self.calls.append((url, params_typed))
        status, payload = self.payloads[station_id]
        return _FakeAnyResponse(status_code=status, payload=payload)

    def __enter__(self) -> _FakeAnySession:  # pragma: no cover - context helper
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:  # pragma: no cover - context helper
        return None


@pytest.mark.parametrize(
    "non_object_payload",
    [
        # A successfully-decoded but non-dict body (list / null / scalar) used
        # to crash ``payload.get("StopLocation")`` with AttributeError, taking
        # the entire per-station loop down with it. The Zero-Trust shape guard
        # makes this fall back through the same path as a HTTP error or a
        # decode failure.
        [],
        None,
        42,
        "Service Unavailable",
    ],
)
def test_fetch_vor_stops_from_api_falls_back_on_non_object_payload(
    monkeypatch: pytest.MonkeyPatch, non_object_payload: Any
) -> None:
    payloads: dict[str, tuple[int, Any]] = {
        "490091000": (200, non_object_payload),
    }
    fake_session = _FakeAnySession(payloads)

    monkeypatch.setattr(module, "session_with_retries", lambda *args, **kwargs: fake_session)
    monkeypatch.setattr(module.vor_provider, "refresh_access_credentials", lambda: "token")
    monkeypatch.setattr(module.vor_provider, "VOR_ACCESS_ID", "token", raising=False)

    fallback = {
        "490091000": module.VORStop(
            vor_id="490091000",
            name="Fallback Stop",
            latitude=None,
            longitude=None,
        )
    }

    stops = module.fetch_vor_stops_from_api(["490091000"], fallback=fallback)

    # The fallback must be used because the malformed payload is treated
    # like a decode failure, not propagated as AttributeError.
    assert [stop.vor_id for stop in stops] == ["490091000"]
    assert [stop.name for stop in stops] == ["Fallback Stop"]


def test_canonical_vor_name_strips_suffixes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module.vor_provider, "STATION_NAME_MAP", {}, raising=False)

    assert module._canonical_vor_name("Wien Karlsplatz U") == "Wien Karlsplatz"
    assert module._canonical_vor_name("Wien Karlsplatz U (VOR)") == "Wien Karlsplatz"
    assert module._canonical_vor_name("Wien Karlsplatz (WL)") == "Wien Karlsplatz"
    assert module._canonical_vor_name("Wien Hauptbahnhof (VOR)") == "Wien Hauptbahnhof"

    mapping = {
        "Vienna Karlsplatz U": "Wien Karlsplatz",
    }
    monkeypatch.setattr(module.vor_provider, "STATION_NAME_MAP", mapping, raising=False)
    assert module._canonical_vor_name("Vienna Karlsplatz U (WL)") == "Wien Karlsplatz"
    assert module._canonical_vor_name("Vienna Karlsplatz") == "Wien Karlsplatz"
