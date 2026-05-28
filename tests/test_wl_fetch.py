import logging
from datetime import datetime, UTC
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

    assert names == ["Wien Franz-Josefs-Bahnhof"]


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
    now = datetime.now(UTC).isoformat()
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
    # New logic: Description includes stop names if present (alphabetical
    # order). After PR #1444 reactivated WL OGD, ``Museumsquartier``
    # resolves through the directory to ``Wien Museumsquartier (WL)``
    # (pre-#1442 only ``Karlsplatz`` had a WL canonical, hence the
    # legacy ``Museumsquartier, Wien Karlsplatz`` ordering — the new
    # output reflects both stops being canonicalised).
    assert (
        events[0]["description"]
        == "Testbeschreibung | Haltestelle: Wien Karlsplatz, Wien Museumsquartier (WL)"
    )


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


def test_fetch_events_tolerates_non_string_title_and_description(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A truthy non-string ``title`` / ``description`` from a misbehaving
    upstream peer must not crash the whole batch.

    Pre-fix the inline ``(ti.get("title") or ti.get("name") or "Meldung").strip()``
    chain raised ``AttributeError`` when the value was an ``int`` / ``list`` /
    ``dict`` (because ``or`` short-circuits to the truthy non-string value and
    ``.strip()`` is then called on it). ``update_wl_cache.py``'s broad
    ``except Exception`` then swallowed the crash and kept the stale cache —
    one bad upstream field disabled the WL refresh for every other item in
    the same batch.
    """
    now = datetime.now(UTC).isoformat()
    bad_traffic_info = {
        "title": 42,
        "description": ["unexpected", "list"],
        "time": {"start": now},
        "attributes": {},
    }
    good_traffic_info = _base_event(title="Sperre Karlsplatz")

    _setup_fetch(
        monkeypatch,
        traffic_infos=[bad_traffic_info, good_traffic_info],
        news=[],
    )

    # Pre-fix this would propagate AttributeError out of fetch_events.
    events = fetch_events(timeout=0)
    # The well-formed item survives; the malformed item must not abort the batch.
    titles = [str(event.get("title", "")) for event in events]
    assert any("Karlsplatz" in title for title in titles)


def test_fetch_events_tolerates_non_string_news_subtitle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-string ``subtitle`` in the news payload must not raise
    ``TypeError`` inside ``" ".join(...)``.

    Pre-fix only ``attrs.get("status")`` / ``attrs.get("state")`` were
    routed through ``str(... or "")``; ``poi.get("subtitle") or ""``
    returned the truthy non-string value directly, so a dict / list /
    int subtitle aborted the entire news loop.
    """
    now = datetime.now(UTC).isoformat()
    bad_news = {
        "title": "Hinweis",
        "subtitle": {"unexpected": "dict-shape"},
        "description": "Description text",
        "time": {"start": now},
        "attributes": {},
    }

    _setup_fetch(monkeypatch, traffic_infos=[], news=[bad_news])

    # Pre-fix this would propagate TypeError out of fetch_events.
    events = fetch_events(timeout=0)
    assert isinstance(events, list)
