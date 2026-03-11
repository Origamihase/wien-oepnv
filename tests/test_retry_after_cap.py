import logging
import requests
import src.providers.vor as vor
import src.providers.oebb as oebb
from datetime import datetime

class DummySession:
    def __init__(self):
        self.headers = {}
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        pass

    def close(self):
        pass

def test_vor_retry_after_capped(monkeypatch, caplog):
    """Verify that VOR provider caps the Retry-After delay."""

    # Mock response with extremely large Retry-After
    def fake_fetch(session, url, **kwargs):
        resp = requests.Response()
        resp.status_code = 429
        resp.headers["Retry-After"] = "99999"
        raise requests.HTTPError(response=resp)

    monkeypatch.setattr(vor, "fetch_content_safe", fake_fetch)
    monkeypatch.setattr(vor, "session_with_retries", lambda *a, **kw: DummySession())

    # Enable logging capture to verify the warning
    caplog.set_level(logging.WARNING, logger=vor.log.name)

    # Trigger the fetch
    vor._fetch_departure_board_for_station("123", datetime(2024, 1, 1, 12, 0))

    # Updated: VOR now uses fail-fast strategy for 429, skipping sleep to avoid thread blocking.
    # We verify the warning log contains the raw Retry-After value
    assert any("Retry-After: 99999.0s" in message for message in caplog.messages)
    assert any("Überspringe Station (Fail-Fast)" in message for message in caplog.messages)

def test_oebb_retry_after_capped(monkeypatch, caplog):
    """Verify that OEBB provider caps the Retry-After delay."""

    def fake_fetch_safe(session, url, **kwargs):
        resp = requests.Response()
        resp.status_code = 429
        resp.headers["Retry-After"] = "99999"
        raise requests.HTTPError(response=resp)

    # We need to patch fetch_content_safe in oebb module scope
    monkeypatch.setattr(oebb, "fetch_content_safe", fake_fetch_safe)
    monkeypatch.setattr(oebb, "session_with_retries", lambda *a, **kw: DummySession())

    caplog.set_level(logging.WARNING, logger=oebb.log.name)

    result = oebb.fetch_events()

    assert len(result) == 0
    assert any("Fail-Fast" in message for message in caplog.messages)
