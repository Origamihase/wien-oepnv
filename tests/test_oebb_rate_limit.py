import logging

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
