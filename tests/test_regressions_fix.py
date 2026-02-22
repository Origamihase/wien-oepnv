import pytest
import re
from datetime import datetime, timezone
from src.providers.wl_fetch import fetch_events as wl_fetch_events
from src.providers.oebb import fetch_events as oebb_fetch_events
from defusedxml import ElementTree as ET

# --- Wiener Linien Mocks ---

class DummySession:
    def __init__(self):
        self.headers = {}
    def __enter__(self): return self
    def __exit__(self, *args): pass

def _setup_wl_fetch(monkeypatch, traffic_infos=None, news=None):
    monkeypatch.setattr("src.providers.wl_fetch._fetch_traffic_infos", lambda *a, **kw: traffic_infos or [])
    monkeypatch.setattr("src.providers.wl_fetch._fetch_news", lambda *a, **kw: news or [])
    monkeypatch.setattr("src.providers.wl_fetch.session_with_retries", lambda *a, **kw: DummySession())
    # Mock date correction
    monkeypatch.setattr("src.providers.wl_fetch.extract_date_from_title", lambda *a, **kw: None)

def _base_wl_event(**overrides):
    # Set start in the past so it's active
    base = {
        "title": "Störung",
        "description": "Test",
        "time": {"start": "2023-01-01T10:00:00Z"},
        "attributes": {}
    }
    base.update(overrides)
    return base

# --- ÖBB Mocks ---

def _setup_oebb_fetch(monkeypatch, xml_content):
    root = ET.fromstring(xml_content)
    monkeypatch.setattr("src.providers.oebb._fetch_xml", lambda *a, **kw: root)
    monkeypatch.setattr("src.providers.oebb._is_relevant", lambda *a, **kw: True)


# --- Tests ---

def test_wl_preserves_html_tags(monkeypatch):
    """
    Test that HTML tags in Wiener Linien descriptions are preserved.
    """
    html_desc = "<h2>Kranarbeiten</h2> <p>Wegen Kranarbeiten...</p>"
    traffic_info = _base_wl_event(description=html_desc)

    _setup_wl_fetch(monkeypatch, traffic_infos=[traffic_info])

    events = wl_fetch_events(timeout=0)

    assert len(events) == 1
    # Check that tags are present
    assert "<h2>Kranarbeiten</h2>" in events[0]["description"]
    assert "<p>" in events[0]["description"]
    # Check that they are NOT stripped (e.g. h2Kranarbeiten/h2)
    # This assertion will FAIL if the regression is present
    assert "h2Kranarbeiten/h2" not in events[0]["description"]


def test_oebb_extracts_line_with_html_tags(monkeypatch):
    """
    Test that ÖBB line extraction works when the line name is wrapped in HTML tags.
    e.g. <i>REX 1</i>
    """
    xml = """
    <rss version="2.0">
    <channel>
        <item>
            <title>Wien Meidling ↔ Wien Hauptbahnhof</title>
            <link>http://example.com</link>
            <description>Aufgrund von Bauarbeiten fallen &lt;i&gt;REX 1&lt;/i&gt; Züge aus.</description>
            <pubDate>Mon, 01 Jan 2024 10:00:00 GMT</pubDate>
            <guid>123</guid>
        </item>
    </channel>
    </rss>
    """

    _setup_oebb_fetch(monkeypatch, xml)

    events = oebb_fetch_events(timeout=0)

    assert len(events) == 1
    # The title should be updated with the line info
    assert events[0]["title"].startswith("REX 1:")


def test_oebb_extracts_line_with_split_html_tags(monkeypatch):
    """
    Test that ÖBB line extraction works when the line name is split by HTML tags.
    e.g. <b>REX</b> <i>1</i>
    """
    xml = """
    <rss version="2.0">
    <channel>
        <item>
            <title>Wien Meidling ↔ Wien Hauptbahnhof</title>
            <link>http://example.com</link>
            <description>Aufgrund von Bauarbeiten fallen &lt;b&gt;REX&lt;/b&gt; &lt;i&gt;1&lt;/i&gt; Züge aus.</description>
            <pubDate>Mon, 01 Jan 2024 10:00:00 GMT</pubDate>
            <guid>124</guid>
        </item>
    </channel>
    </rss>
    """

    _setup_oebb_fetch(monkeypatch, xml)

    events = oebb_fetch_events(timeout=0)

    assert len(events) == 1
    # This assertion will FAIL if the regression is present
    assert events[0]["title"].startswith("REX 1:")
