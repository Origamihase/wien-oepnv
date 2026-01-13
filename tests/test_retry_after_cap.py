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

    sleep_calls: list[float] = []
    def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr(vor.time, "sleep", fake_sleep)

    # Enable logging capture to verify the warning
    caplog.set_level(logging.WARNING, logger=vor.log.name)

    # Trigger the fetch
    vor._fetch_stationboard("123", datetime(2024, 1, 1, 12, 0))

    # Expect the sleep to be capped (assuming we will set it to 120)
    assert len(sleep_calls) > 0
    assert sleep_calls[0] <= 120.0
    assert any("zu hoch" in message for message in caplog.messages) or any("kappe auf" in message for message in caplog.messages)

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

    sleep_calls: list[float] = []
    def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr(oebb.time, "sleep", fake_sleep)

    caplog.set_level(logging.WARNING, logger=oebb.log.name)

    oebb.fetch_events()

    assert len(sleep_calls) > 0
    assert sleep_calls[0] <= 120.0
