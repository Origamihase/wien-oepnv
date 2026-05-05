import logging
from datetime import datetime, timezone
from types import TracebackType
from typing import Any, Literal

import pytest

from src.providers.wl_fetch import _stop_names_from_related, fetch_events


def test_stop_names_from_related_uses_canonical_names() -> None:
    rel_stops = [
        {"name": "Wien Franz Josefs Bahnhof"},
        {"stopName": "Wien Franz-Josefs-Bf"},
        " Wien Franz Josefs Bahnhof ",
    ]

    names = _stop_names_from_related(rel_stops)

    assert names == ["Wien Franz-Josefs-Bf"]


def test_fetch_events_handles_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class DummyResponse:
        headers = {"Content-Type": "application/json"}
        def raise_for_status(self) -> None:
            return None

        def json(self) -> Any:
            raise ValueError("invalid JSON")

        def iter_content(self, chunk_size: int = 8192) -> list[bytes]:
            return [b"invalid"]

        @property
        def content(self) -> bytes:
            return b"invalid"

        def __enter__(self) -> "DummyResponse":
            return self

        def __exit__(self, *args: Any) -> None:
            pass

    class DummySession:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}

        def __enter__(self) -> "DummySession":
            return self

        def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> Literal[False]:
            return False

        def prepare_request(self, request: Any) -> Any:
            from requests.models import PreparedRequest
            p = PreparedRequest()
            p.prepare(
                method=request.method,
                url=request.url,
                headers=request.headers,
                files=request.files,
                data=request.data,
                json=request.json,
                params=request.params,
                auth=request.auth,
                cookies=request.cookies,
                hooks=request.hooks,
            )
            return p

        def merge_environment_settings(self, url: Any, proxies: Any, stream: Any, verify: Any, cert: Any) -> dict[str, Any]:
            return {}

        def get(self, url: str, params: Any = None, timeout: Any = None, stream: bool = False, **kwargs: Any) -> Any:
            return DummyResponse()

        def request(self, method: str, url: str, **kwargs: Any) -> Any:
            return self.get(url, **kwargs)

    monkeypatch.setattr("src.providers.wl_fetch.session_with_retries", lambda *a, **kw: DummySession())

    # Mock fetch_content_safe to avoid real network/pinned adapter logic which fails with timeout=0
    def fake_fetch_content_safe(*args: Any, **kwargs: Any) -> None:
        raise ValueError("Ungültige JSON-Antwort")

    monkeypatch.setattr("src.providers.wl_fetch.fetch_content_safe", fake_fetch_content_safe)

    with caplog.at_level(logging.WARNING):
        events = fetch_events(timeout=0)

    assert events == []
    # With fetch_content_safe, invalid JSON will result in json.loads failing, which is caught.
    # The message includes "Ungültige JSON-Antwort" or "Antwort ... zu groß oder ungültig" or the new consolidated message
    assert any(
        (
            "ungültig oder kein JSON" in message
            or "Ungültige JSON-Antwort" in message
            or "zu groß oder ungültig" in message
        )
        for message in caplog.messages
    )


class DummySession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}

    def __enter__(self) -> "DummySession":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        return False


def _setup_fetch(
    monkeypatch: pytest.MonkeyPatch,
    traffic_infos: list[dict[str, Any]] | None = None,
    news: list[dict[str, Any]] | None = None,
) -> None:
    monkeypatch.setattr(
        "src.providers.wl_fetch._fetch_traffic_infos",
        lambda *a, **kw: traffic_infos or [],
    )
    monkeypatch.setattr(
        "src.providers.wl_fetch._fetch_news",
        lambda *a, **kw: news or [],
    )
    monkeypatch.setattr(
        "src.providers.wl_fetch.session_with_retries",
        lambda *a, **kw: DummySession(),
    )


# Any: overrides accepts arbitrary kwargs; values vary per test
def _base_event(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    base = {
        "title": "Sperre Museumsquartier",
        "description": "Testbeschreibung",
        "time": {"start": now},
        "attributes": {},
    }
    base.update(overrides)
    return base


def test_fetch_events_adds_stop_context_when_no_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    rel_stops = [
        {"name": "Karlsplatz"},
        {"name": "Museumsquartier"},
    ]
    traffic_info = _base_event(
        attributes={
            "station": "Museumsquartier (U2)",
            "relatedStops": rel_stops,
        }
    )

    _setup_fetch(monkeypatch, traffic_infos=[traffic_info], news=[])

    events = fetch_events(timeout=0)

    assert len(events) == 1
    title = events[0]["title"]
    assert " – " in title
    assert "Karlsplatz" in title
    assert "Museumsquartier" in title
    assert title.endswith("(2 Halte)")
    # New logic: Description includes stop names if present
    assert events[0]["description"] == "Testbeschreibung | Haltestelle: Museumsquartier, Wien Karlsplatz"


def test_fetch_events_uses_extra_context_when_no_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    traffic_info = _base_event(
        attributes={
            "station": "Karlsplatz",
            "location": "Ausgang Oper",
        }
    )

    _setup_fetch(monkeypatch, traffic_infos=[traffic_info], news=[])

    events = fetch_events(timeout=0)

    assert len(events) == 1
    title = events[0]["title"]
    assert "Halte" not in title  # keine Halteanzahl bei fehlenden Stopps
    assert " – Karlsplatz" in title
    assert "Ausgang Oper" in title
    desc = events[0]["description"]
    assert "Station: Karlsplatz" in desc
    assert "Location: Ausgang Oper" in desc
