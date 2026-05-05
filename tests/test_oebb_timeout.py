import xml.etree.ElementTree as ET
from typing import Any, Iterator

import pytest

import src.providers.oebb as oebb


def test_fetch_events_passes_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded = {}

    def fake_fetch_xml(url: str, timeout: Any) -> ET.Element:
        recorded["timeout"] = timeout
        root = ET.Element("rss")
        ET.SubElement(root, "channel")
        return root

    monkeypatch.setattr(oebb, "_fetch_xml", fake_fetch_xml)

    result = oebb.fetch_events(timeout=7)
    assert result == []
    assert recorded["timeout"] == 7


def test_fetch_xml_passes_timeout_to_session(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded = {}

    class DummyResponse:
        content = b"<rss/>"
        status_code = 200
        headers: dict[str, str] = {}

        def raise_for_status(self) -> None:
            pass

        def iter_content(self, chunk_size: int = 8192) -> Iterator[bytes]:
            yield self.content

        def __enter__(self) -> "DummyResponse":
            return self

        def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            pass

    class DummySession:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}

        def __enter__(self) -> "DummySession":
            return self

        def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            pass

        def get(self, url: str, timeout: Any, stream: bool = False, **kwargs: Any) -> Any:
            recorded["timeout"] = timeout
            recorded["stream"] = stream
            return DummyResponse()

        def request(self, method: str, url: str, timeout: Any = None, stream: bool = False, **kwargs: Any) -> Any:
            return self.get(url, timeout=timeout, stream=stream, **kwargs)
    monkeypatch.setattr(oebb, "session_with_retries", lambda *a, **kw: DummySession())

    oebb._fetch_xml("http://example.com", timeout=3)
    assert 2.9 <= recorded["timeout"] <= 3
    assert recorded["stream"] is True
