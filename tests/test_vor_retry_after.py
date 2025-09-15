import logging
from datetime import datetime, timedelta, timezone

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


def test_retry_after_numeric_value(monkeypatch):
    class DummyResponse:
        status_code = 429
        headers = {"Retry-After": "3.5"}
        content = b""

    class DummySession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            pass

        def get(self, url, params, timeout):
            return DummyResponse()

    monkeypatch.setattr(vor, "_session", lambda: DummySession())

    sleep_calls: list[float] = []

    def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr(vor.time, "sleep", fake_sleep)

    result = vor._fetch_stationboard("123", datetime(2024, 1, 1, 12, 0))

    assert result is None
    assert sleep_calls == [3.5]


def test_retry_after_http_date(monkeypatch):
    fixed_now = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    delay = timedelta(seconds=7)
    retry_dt = fixed_now + delay

    class DummyResponse:
        status_code = 429
        headers = {"Retry-After": retry_dt.strftime("%a, %d %b %Y %H:%M:%S GMT")}
        content = b""

    class DummySession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            pass

        def get(self, url, params, timeout):
            return DummyResponse()

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == timezone.utc
            return fixed_now

    monkeypatch.setattr(vor, "_session", lambda: DummySession())
    monkeypatch.setattr(vor, "datetime", FixedDateTime)

    sleep_calls: list[float] = []

    def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr(vor.time, "sleep", fake_sleep)

    result = vor._fetch_stationboard("123", datetime(2024, 1, 1, 12, 0))

    assert result is None
    assert sleep_calls == [delay.total_seconds()]


def test_fetch_stationboard_sends_products_param(monkeypatch):
    captured_params: dict[str, object] = {}

    class DummyResponse:
        status_code = 200
        headers: dict[str, str] = {}

        @staticmethod
        def json():
            return {}

    class DummySession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            pass

        def get(self, url, params, timeout):
            captured_params.update(params)
            return DummyResponse()

    monkeypatch.setattr(vor, "_session", lambda: DummySession())
    monkeypatch.setattr(vor, "ALLOW_BUS", False)

    result = vor._fetch_stationboard("123", datetime(2024, 1, 1, 12, 0))

    assert result == {}
    assert "products" in captured_params
    expected_mask = vor._product_class_bitmask(vor._desired_product_classes())
    assert captured_params["products"] == str(expected_mask)
