import importlib
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import src.providers.vor as vor


def test_accessid_not_logged(monkeypatch, caplog):
    monkeypatch.setenv("VOR_ACCESS_ID", "secret")
    importlib.reload(vor)

    class DummySession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, *args, **kwargs):
            raise requests.RequestException(f"boom accessId={vor.VOR_ACCESS_ID}")

    monkeypatch.setattr(vor, "_session", lambda: DummySession())
    now_local = datetime.now(ZoneInfo("Europe/Vienna"))

    with caplog.at_level(logging.ERROR):
        vor._fetch_stationboard("123", now_local)

    assert f"accessId={vor.VOR_ACCESS_ID}" not in caplog.text
    assert "accessId=***" in caplog.text

    monkeypatch.delenv("VOR_ACCESS_ID", raising=False)
    importlib.reload(vor)
