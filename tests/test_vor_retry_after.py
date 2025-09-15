import logging
from datetime import datetime

import src.providers.vor as vor


def test_retry_after_invalid_value(monkeypatch, caplog):
    class DummyResponse:
        status_code = 429
        headers = {"Retry-After": "not-a-number"}
        content = b""

    class DummySession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            pass

        def get(self, url, params, timeout):
            return DummyResponse()

    monkeypatch.setattr(vor, "_session", lambda: DummySession())

    def fake_sleep(seconds):
        raise AssertionError("sleep should not be called")

    monkeypatch.setattr(vor.time, "sleep", fake_sleep)

    caplog.set_level(logging.WARNING, logger=vor.log.name)

    result = vor._fetch_stationboard("123", datetime(2024, 1, 1, 12, 0))

    assert result is None
    assert any("ungültiges Retry-After" in message for message in caplog.messages)
