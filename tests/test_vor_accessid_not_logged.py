import importlib
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import requests
import src.providers.vor as vor


@pytest.mark.parametrize(
    ("raw_message", "expected_fragment"),
    [
        ("boom accessId=secret", "accessId=***"),
        ("boom {'accessId': 'secret'}", "'accessId': '***'"),
        ('boom "accessId":"secret"', '"accessId":"***"'),
        ("boom accessId%3Dsecret&foo", "accessId%3D***"),
        ("boom Authorization: Bearer secret", "Authorization: ***"),
        ('boom {"Authorization": "Bearer secret"}', '"Authorization": "***"'),
        ("boom Authorization: Basic secret", "Authorization: ***"),
        ('boom {"Authorization": "Basic secret"}', '"Authorization": "***"'),
    ],
)
def test_accessid_not_logged(monkeypatch, caplog, raw_message, expected_fragment):
    monkeypatch.setenv("VOR_ACCESS_ID", "secret")
    importlib.reload(vor)

    def _make_session():
        class DummySession:
            def __init__(self):
                self.headers: dict[str, str] = {}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def request(self, method, url, **kwargs):
                raise requests.RequestException(raw_message)

            def get(self, url, **kwargs):
                return self.request("GET", url, **kwargs)

        return DummySession()

    monkeypatch.setattr(vor, "session_with_retries", lambda *a, **kw: _make_session())
    now_local = datetime.now(ZoneInfo("Europe/Vienna"))

    with caplog.at_level(logging.ERROR):
        vor._fetch_traffic_info("123", now_local)

    assert vor.VOR_ACCESS_ID not in caplog.text
    assert expected_fragment in caplog.text

    if vor.REQUEST_COUNT_FILE.exists():
        vor.REQUEST_COUNT_FILE.unlink()

    monkeypatch.delenv("VOR_ACCESS_ID", raising=False)
    importlib.reload(vor)
