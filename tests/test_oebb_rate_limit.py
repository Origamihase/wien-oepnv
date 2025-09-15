import logging

import src.providers.oebb as oebb


def test_oebb_fetch_events_handles_rate_limit(monkeypatch, caplog):
    sleep_calls = []

    class DummyResponse:
        status_code = 429
        headers = {"Retry-After": "2.5"}
        content = b""

    class DummySession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url, timeout):
            return DummyResponse()

    monkeypatch.setattr(oebb, "_session", lambda: DummySession())
    monkeypatch.setattr(oebb.time, "sleep", lambda delay: sleep_calls.append(delay))

    with caplog.at_level(logging.DEBUG):
        items = oebb.fetch_events(timeout=3)

    assert items == []
    assert sleep_calls == [2.5]
    for rec in caplog.records:
        assert oebb.OEBB_URL not in rec.getMessage()
