
import pytest
from datetime import datetime, timezone
import xml.etree.ElementTree as ET
from src.providers.oebb import fetch_events

class DummySession:
    def __init__(self):
        self.headers: dict[str, str] = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

def test_oebb_empty_title_fallback(monkeypatch):
    # Simulate RSS with empty title that results in " - "
    rss_content = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>ÖBB Streckeninfo</title>
<item>
    <title><![CDATA[ - ]]></title>
    <link>http://fahrplan.oebb.at/bin/help.exe/dnl?tpl=showmap_external&amp;param=station_silberwald</link>
    <description><![CDATA[Station Silberwald: Aufzug außer Betrieb]]></description>
    <pubDate>Mon, 20 Dec 2025 08:00:00 GMT</pubDate>
    <guid>oebb_silberwald</guid>
</item>
</channel>
</rss>
"""

    monkeypatch.setattr("src.providers.oebb._fetch_xml", lambda *a, **kw: ET.fromstring(rss_content))
    monkeypatch.setattr("src.providers.oebb._keep_by_region", lambda *a: True)

    events = fetch_events()
    assert len(events) == 1
    ev = events[0]

    # We expect the fallback logic to pick up "Station Silberwald" or similar
    assert ev['title'] != "-", "Title matches just hyphen, fallback failed"
    assert "Silberwald" in ev['title'], "Title should contain station name from description"
