import logging
from datetime import datetime

import src.providers.vor as vor


def test_vor_retry_after_invalid_header(monkeypatch, caplog):
    sleep_calls = []

    class DummyResponse:
        status_code = 429
        headers = {"Retry-After": "n/a"}
        content = b""

    class DummySession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url, params, timeout):
            return DummyResponse()

    monkeypatch.setattr(vor, "_session", lambda: DummySession())
    monkeypatch.setattr(vor.time, "sleep", lambda delay: sleep_calls.append(delay))

    now = datetime(2024, 1, 1, 8, 0, 0)
    with caplog.at_level(logging.WARNING):
        result = vor._fetch_stationboard("123", now)

    assert result is None
    assert sleep_calls == []
    assert any("ungültiges Retry-After" in rec.getMessage() for rec in caplog.records)
