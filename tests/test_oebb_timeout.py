import xml.etree.ElementTree as ET

import src.providers.oebb as oebb


def test_fetch_events_passes_timeout(monkeypatch):
    recorded = {}

    def fake_fetch_xml(url, timeout):
        recorded["timeout"] = timeout
        root = ET.Element("rss")
        ET.SubElement(root, "channel")
        return root

    monkeypatch.setattr(oebb, "_fetch_xml", fake_fetch_xml)

    result = oebb.fetch_events(timeout=7)
    assert result == []
    assert recorded["timeout"] == 7


def test_fetch_xml_passes_timeout_to_session(monkeypatch):
    recorded = {}

    class DummyResponse:
        content = b"<rss/>"
        status_code = 200
        headers = {}

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size=8192):
            yield self.content

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            pass

    class DummySession:
        def __init__(self):
            self.headers: dict[str, str] = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            pass

        def get(self, url, timeout, stream=False, **kwargs):
            recorded["timeout"] = timeout
            recorded["stream"] = stream
            return DummyResponse()
    monkeypatch.setattr(oebb, "session_with_retries", lambda *a, **kw: DummySession())

    oebb._fetch_xml("http://example.com", timeout=3)
    assert recorded["timeout"] == 3
    assert recorded["stream"] is True
