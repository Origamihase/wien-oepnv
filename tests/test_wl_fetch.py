import logging
from datetime import datetime, timezone

from src.providers.wl_fetch import _stop_names_from_related, fetch_events


def test_stop_names_from_related_uses_canonical_names():
    rel_stops = [
        {"name": "Wien Franz Josefs Bahnhof"},
        {"stopName": "Wien Franz-Josefs-Bf"},
        " Wien Franz Josefs Bahnhof ",
    ]

    names = _stop_names_from_related(rel_stops)

    assert names == ["Wien Franz-Josefs-Bf"]


def test_fetch_events_handles_invalid_json(monkeypatch, caplog):
    class DummyResponse:
        headers = {}
        def raise_for_status(self):
            return None

        def json(self):
            raise ValueError("invalid JSON")

        def iter_content(self, chunk_size=8192):
            return [b"invalid"]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class DummySession:
        def __init__(self):
            self.headers: dict[str, str] = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url, params=None, timeout=None, stream=False, **kwargs):
            return DummyResponse()

    monkeypatch.setattr("src.providers.wl_fetch.session_with_retries", lambda *a, **kw: DummySession())

    with caplog.at_level(logging.WARNING):
        events = fetch_events(timeout=0)

    assert events == []
    # With fetch_content_safe, invalid JSON will result in json.loads failing, which is caught.
    # The message includes "Ungültige JSON-Antwort" or "Antwort ... zu groß oder ungültig"
    assert any(("Ungültige JSON-Antwort" in message or "zu groß oder ungültig" in message) for message in caplog.messages)


class DummySession:
    def __init__(self):
        self.headers: dict[str, str] = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _setup_fetch(monkeypatch, traffic_infos=None, news=None):
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


def _base_event(**overrides):
    now = datetime.now(timezone.utc).isoformat()
    base = {
        "title": "Sperre Museumsquartier",
        "description": "Testbeschreibung",
        "time": {"start": now},
        "attributes": {},
    }
    base.update(overrides)
    return base


def test_fetch_events_adds_stop_context_when_no_lines(monkeypatch):
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
    assert "Station: Museumsquartier (U2)" in events[0]["description"]


def test_fetch_events_uses_extra_context_when_no_stops(monkeypatch):
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
    assert "Station: Karlsplatz" not in desc
    assert "Location: Ausgang Oper" not in desc
