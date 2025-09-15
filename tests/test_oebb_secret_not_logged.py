import importlib
import logging
import requests

import src.providers.oebb as oebb


def test_oebb_url_masked_in_error_log(monkeypatch, caplog):
    monkeypatch.setenv("OEBB_RSS_URL", "https://secret.example/?token=abc")
    importlib.reload(oebb)

    def failing_fetch(url, timeout):
        raise requests.RequestException(f"boom {oebb.OEBB_URL}")

    monkeypatch.setattr(oebb, "_fetch_xml", failing_fetch)

    with caplog.at_level(logging.ERROR, logger=oebb.log.name):
        oebb.fetch_events()

    assert oebb.OEBB_URL not in caplog.text
    assert "***" in caplog.text

    monkeypatch.delenv("OEBB_RSS_URL", raising=False)
    importlib.reload(oebb)
