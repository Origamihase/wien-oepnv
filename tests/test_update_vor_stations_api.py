"""Tests for the VOR station directory API integration."""

from __future__ import annotations

import json as _json
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

    def __post_init__(self) -> None:
        body = _json.dumps(self.payload).encode("utf-8")
        self._body = body
        self.headers: dict[str, str] = {"Content-Length": str(len(body))}

    def json(self) -> dict[str, Any]:
        return self.payload

    def iter_content(self, chunk_size: int = 8192) -> Any:
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def close(self) -> None:
        return None

    def raise_for_status(self) -> None:
        return None


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
        **kwargs: object,
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
    """Live-fetch happy path + transient HTTP-error fallback path.

    The 2026-05-09 VOR-quota optimization restricted ``location.name``
    enrichment to the Stammstrecke whitelist, so the test exercises the
    happy/error pair against two Stammstrecke IDs (Floridsdorf success,
    Meidling 500). Non-whitelisted IDs are covered by
    ``test_fetch_vor_stops_from_api_skips_non_stammstrecke_ids`` below.
    """
    payloads = {
        "490033400": (
            200,
            {
                "StopLocation": {
                    "id": "490033400",
                    "name": "Wien Floridsdorf",
                    "coord": {"lat": 48.255, "lon": 16.401},
                    "municipality": "Wien",
                }
            },
        ),
        "490101500": (500, {}),
    }

    fake_session = _FakeSession(payloads)

    monkeypatch.setattr(module, "session_with_retries", lambda *args, **kwargs: fake_session)
    monkeypatch.setattr(module.vor_provider, "refresh_access_credentials", lambda: "token")
    monkeypatch.setattr(module.vor_provider, "VOR_ACCESS_ID", "token", raising=False)

    fallback = {
        "490101500": module.VORStop(
            vor_id="490101500",
            name="Fallback Stop",
            latitude=None,
            longitude=None,
        )
    }

    stops = module.fetch_vor_stops_from_api(["490033400", "490101500"], fallback=fallback)

    # Both IDs are Stammstrecke whitelisted, so the live-fetch loop runs
    # for both. Floridsdorf returns 200 (parsed); Meidling returns 500
    # (falls back to the pinned VORStop). Order: skipped fallbacks first
    # (none in this test), then live-fetch results in input order.
    returned_ids = [stop.vor_id for stop in stops]
    assert sorted(returned_ids) == ["490033400", "490101500"]
    assert any("location.name" in call[0] for call in fake_session.calls)
    assert module.vor_provider.refresh_access_credentials() == "token"


def test_fetch_vor_stops_from_api_skips_non_stammstrecke_ids(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Non-whitelisted IDs must skip the live API call entirely.

    The 2026-05-09 VOR-quota optimization (see ``STAMMSTRECKE_VOR_IDS``
    in ``scripts/update_vor_stations.py``) restricts live enrichment to
    the 10 Stammstrecke S-Bahn stations. Any other ID falls through to
    the pinned-CSV fallback path without consuming a VAO Start request.
    """
    fake_session = _FakeSession({})

    monkeypatch.setattr(module, "session_with_retries", lambda *args, **kwargs: fake_session)
    monkeypatch.setattr(module.vor_provider, "refresh_access_credentials", lambda: "token")
    monkeypatch.setattr(module.vor_provider, "VOR_ACCESS_ID", "token", raising=False)

    fallback = {
        "490091000": module.VORStop(
            vor_id="490091000",
            name="Wien Aspern Nord (pinned CSV)",
            latitude=48.234567,
            longitude=16.520123,
        ),
        "430470800": module.VORStop(
            vor_id="430470800",
            name="Flughafen Wien (pinned CSV)",
            latitude=None,
            longitude=None,
        ),
    }

    with caplog.at_level("INFO", logger=module.log.name):
        stops = module.fetch_vor_stops_from_api(
            ["490091000", "430470800"], fallback=fallback
        )

    assert sorted(stop.vor_id for stop in stops) == ["430470800", "490091000"]
    # Crucially: NO live API calls — both IDs were skipped because
    # they are outside the Stammstrecke whitelist.
    assert fake_session.calls == []
    assert any(
        "Stammstrecke" in record.getMessage() for record in caplog.records
    )


@dataclass
class _FakeAnyResponse:
    """Like _FakeResponse but lets us return non-dict payloads from .json()."""

    status_code: int
    payload: Any

    def __post_init__(self) -> None:
        # Best-effort serialise so the streamed body matches the json() shape.
        try:
            body = _json.dumps(self.payload).encode("utf-8")
        except (TypeError, ValueError):
            body = repr(self.payload).encode("utf-8")
        self._body = body
        self.headers: dict[str, str] = {"Content-Length": str(len(body))}

    def json(self) -> Any:
        return self.payload

    def iter_content(self, chunk_size: int = 8192) -> Any:
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def close(self) -> None:
        return None

    def raise_for_status(self) -> None:
        return None


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
        **kwargs: object,
    ) -> _FakeAnyResponse:
        station_id = params["input"]
        self.calls.append((url, params))
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
    # Use a Stammstrecke-whitelisted ID so the live-fetch path is
    # exercised (non-whitelisted IDs would short-circuit to the
    # fallback before reaching the malformed-payload branch).
    payloads: dict[str, tuple[int, Any]] = {
        "490033400": (200, non_object_payload),
    }
    fake_session = _FakeAnySession(payloads)

    monkeypatch.setattr(module, "session_with_retries", lambda *args, **kwargs: fake_session)
    monkeypatch.setattr(module.vor_provider, "refresh_access_credentials", lambda: "token")
    monkeypatch.setattr(module.vor_provider, "VOR_ACCESS_ID", "token", raising=False)

    fallback = {
        "490033400": module.VORStop(
            vor_id="490033400",
            name="Fallback Stop",
            latitude=None,
            longitude=None,
        )
    }

    stops = module.fetch_vor_stops_from_api(["490033400"], fallback=fallback)

    # The fallback must be used because the malformed payload is treated
    # like a decode failure, not propagated as AttributeError.
    assert [stop.vor_id for stop in stops] == ["490033400"]
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
