import logging

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
        def raise_for_status(self):
            return None

        def json(self):
            raise ValueError("invalid JSON")

    class DummySession:
        def __init__(self):
            self.headers: dict[str, str] = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url, params=None, timeout=None):
            return DummyResponse()

    monkeypatch.setattr("src.providers.wl_fetch.session_with_retries", lambda *a, **kw: DummySession())

    with caplog.at_level(logging.WARNING):
        events = fetch_events(timeout=0)

    assert events == []
    assert any("Ungültige JSON-Antwort" in message for message in caplog.messages)
