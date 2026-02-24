import logging
from datetime import datetime, timedelta, timezone

import requests
import src.providers.vor as vor


class DummySession:
    def __init__(self):
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        pass

    def close(self):
        pass


def test_retry_after_invalid_value(monkeypatch, caplog):
    def fake_fetch(session, url, **kwargs):
        resp = requests.Response()
        resp.status_code = 429
        resp.headers["Retry-After"] = "not-a-number"
        raise requests.HTTPError(response=resp)

    monkeypatch.setattr(vor, "fetch_content_safe", fake_fetch)
    monkeypatch.setattr(vor, "session_with_retries", lambda *a, **kw: DummySession())

    caplog.set_level(logging.WARNING, logger=vor.log.name)

    result = vor._fetch_departure_board_for_station("123", datetime(2024, 1, 1, 12, 0))

    assert result is None
    # Logged warning for invalid Retry-After (from _parse_retry_after)
    assert any("ungültiges Retry-After" in message for message in caplog.messages)
    # Logged fail-fast warning
    assert any("Überspringe Station (Fail-Fast)" in message for message in caplog.messages)


def test_retry_after_missing_header(monkeypatch, caplog):
    def fake_fetch(session, url, **kwargs):
        resp = requests.Response()
        resp.status_code = 429
        # No Retry-After header
        raise requests.HTTPError(response=resp)

    monkeypatch.setattr(vor, "fetch_content_safe", fake_fetch)
    monkeypatch.setattr(vor, "session_with_retries", lambda *a, **kw: DummySession())

    caplog.set_level(logging.WARNING, logger=vor.log.name)

    result = vor._fetch_departure_board_for_station("123", datetime(2024, 1, 1, 12, 0))

    assert result is None
    assert any("Retry-After fehlt" in message for message in caplog.messages)
    # Logged fail-fast warning
    assert any("Überspringe Station (Fail-Fast)" in message for message in caplog.messages)


def test_retry_after_numeric_value(monkeypatch, caplog):
    def fake_fetch(session, url, **kwargs):
        resp = requests.Response()
        resp.status_code = 429
        resp.headers["Retry-After"] = "3.5"
        raise requests.HTTPError(response=resp)

    monkeypatch.setattr(vor, "fetch_content_safe", fake_fetch)
    monkeypatch.setattr(vor, "session_with_retries", lambda *a, **kw: DummySession())

    caplog.set_level(logging.WARNING, logger=vor.log.name)

    result = vor._fetch_departure_board_for_station("123", datetime(2024, 1, 1, 12, 0))

    assert result is None
    # Logged warning with delay
    assert any("Retry-After: 3.5s" in message for message in caplog.messages)


def test_retry_after_http_date(monkeypatch, caplog):
    fixed_now = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    delay = timedelta(seconds=7)
    retry_dt = fixed_now + delay

    def fake_fetch(session, url, **kwargs):
        resp = requests.Response()
        resp.status_code = 429
        resp.headers["Retry-After"] = retry_dt.strftime("%a, %d %b %Y %H:%M:%S GMT")
        raise requests.HTTPError(response=resp)

    monkeypatch.setattr(vor, "fetch_content_safe", fake_fetch)
    monkeypatch.setattr(vor, "session_with_retries", lambda *a, **kw: DummySession())
    monkeypatch.setattr(vor, "save_request_count", lambda *a, **kw: None)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            # Allow UTC or Vienna
            if tz is not None:
                return fixed_now.astimezone(tz)
            return fixed_now

    monkeypatch.setattr(vor, "datetime", FixedDateTime)

    caplog.set_level(logging.WARNING, logger=vor.log.name)

    result = vor._fetch_departure_board_for_station("123", datetime(2024, 1, 1, 12, 0))

    assert result is None
    # Logged warning with delay
    assert any("Retry-After: 7.0s" in message for message in caplog.messages)
