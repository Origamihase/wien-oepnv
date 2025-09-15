import logging
from datetime import datetime, timedelta, timezone

import src.providers.oebb as oebb


def test_rate_limit_logs_and_sleeps(monkeypatch, caplog):
    class DummyResponse:
        status_code = 429
        headers = {"Retry-After": "1.5"}
        content = b""

    class DummySession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            pass

        def get(self, url, timeout):
            return DummyResponse()

    monkeypatch.setattr(oebb, "_session", lambda: DummySession())

    slept = []

    def fake_sleep(seconds):
        slept.append(seconds)
        raise RuntimeError("sleep failed")

    monkeypatch.setattr(oebb.time, "sleep", fake_sleep)

    caplog.set_level(logging.WARNING, logger=oebb.log.name)

    result = oebb._fetch_xml("https://example.com", timeout=1)

    assert result is None
    assert slept == [1.5]

    log_text = caplog.text
    assert "Rate-Limit" in log_text
    assert "https://example.com" not in log_text
    assert oebb.OEBB_URL not in log_text


def test_rate_limit_http_date(monkeypatch):
    now_utc = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    class DummyDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now_utc

    retry_dt = now_utc + timedelta(seconds=3)
    header = retry_dt.strftime("%a, %d %b %Y %H:%M:%S GMT")

    class DummyResponse:
        status_code = 429
        headers = {"Retry-After": header}
        content = b""

    class DummySession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            pass

        def get(self, url, timeout):
            return DummyResponse()

    monkeypatch.setattr(oebb, "datetime", DummyDateTime)
    monkeypatch.setattr(oebb, "_session", lambda: DummySession())

    slept = []
    monkeypatch.setattr(oebb.time, "sleep", lambda s: slept.append(s))

    result = oebb._fetch_xml("https://example.com", timeout=1)

    assert result is None
    assert slept == [3.0]
