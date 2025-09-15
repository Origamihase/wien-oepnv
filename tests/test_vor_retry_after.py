import logging
from datetime import datetime, timedelta, timezone

import src.providers.vor as vor


def _dummy_session(response):
    class DummySession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            pass

        def get(self, url, params, timeout):
            return response

    return DummySession()


def test_retry_after_seconds(monkeypatch):
    class DummyResponse:
        status_code = 429
        headers = {"Retry-After": "2.5"}
        content = b""

    monkeypatch.setattr(vor, "_session", lambda: _dummy_session(DummyResponse()))

    slept = []
    monkeypatch.setattr(vor.time, "sleep", lambda s: slept.append(s))

    result = vor._fetch_stationboard("123", datetime(2024, 1, 1, 12, 0))

    assert result is None
    assert slept == [2.5]


def test_retry_after_http_date(monkeypatch):
    now_utc = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    class DummyDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now_utc

    retry_dt = now_utc + timedelta(seconds=5)
    header = retry_dt.strftime("%a, %d %b %Y %H:%M:%S GMT")

    class DummyResponse:
        status_code = 429
        headers = {"Retry-After": header}
        content = b""

    monkeypatch.setattr(vor, "datetime", DummyDateTime)
    monkeypatch.setattr(vor, "_session", lambda: _dummy_session(DummyResponse()))

    slept = []
    monkeypatch.setattr(vor.time, "sleep", lambda s: slept.append(s))

    result = vor._fetch_stationboard("123", now_utc)

    assert result is None
    assert slept == [5.0]


def test_retry_after_invalid_value(monkeypatch, caplog):
    class DummyResponse:
        status_code = 429
        headers = {"Retry-After": "not-a-number"}
        content = b""

    monkeypatch.setattr(vor, "_session", lambda: _dummy_session(DummyResponse()))

    def fake_sleep(seconds):
        raise AssertionError("sleep should not be called")

    monkeypatch.setattr(vor.time, "sleep", fake_sleep)

    caplog.set_level(logging.WARNING, logger=vor.log.name)

    result = vor._fetch_stationboard("123", datetime(2024, 1, 1, 12, 0))

    assert result is None
    assert any("ungültiges Retry-After" in message for message in caplog.messages)
