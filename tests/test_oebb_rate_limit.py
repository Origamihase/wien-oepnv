import logging

import src.providers.oebb as oebb


class DummyResponse:
    def __init__(self, status_code, headers=None, content=b""):
        self.status_code = status_code
        self.headers = headers or {}
        self.content = content


class DummySession:
    def __init__(self, responses, calls):
        self._responses = iter(responses)
        self._calls = calls

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        pass

    def get(self, url, timeout):
        self._calls.append((url, timeout))
        return next(self._responses)


def test_rate_limit_retries_once_after_wait(monkeypatch, caplog):
    responses = [
        DummyResponse(429, {"Retry-After": "1.5"}),
        DummyResponse(200, {}, b"<root></root>"),
    ]
    calls = []
    monkeypatch.setattr(oebb, "_session", lambda: DummySession(responses, calls))

    slept = []

    def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(oebb.time, "sleep", fake_sleep)

    caplog.set_level(logging.WARNING, logger=oebb.log.name)

    result = oebb._fetch_xml("https://example.com", timeout=1)

    assert result is not None
    assert result.tag == "root"
    assert calls == [("https://example.com", 1), ("https://example.com", 1)]
    assert slept == [1.5]

    log_text = caplog.text
    assert "Rate-Limit" in log_text
    assert "https://example.com" not in log_text
    assert oebb.OEBB_URL not in log_text


def test_rate_limit_returns_none_after_retry(monkeypatch):
    responses = [
        DummyResponse(429, {"Retry-After": "1.5"}),
        DummyResponse(429, {"Retry-After": "2"}),
    ]
    calls = []
    monkeypatch.setattr(oebb, "_session", lambda: DummySession(responses, calls))

    slept = []

    def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(oebb.time, "sleep", fake_sleep)

    result = oebb._fetch_xml("https://example.com", timeout=1)

    assert result is None
    assert calls == [("https://example.com", 1), ("https://example.com", 1)]
    assert slept == [1.5]
