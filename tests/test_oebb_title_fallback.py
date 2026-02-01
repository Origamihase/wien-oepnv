
import pytest
from unittest.mock import MagicMock, patch
from src.providers.oebb import fetch_events
from defusedxml import ElementTree as ET

# Mock XML structure
def mock_xml_response(items):
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
        <channel>
            <title>ÖBB-Streckeninfo</title>
            %s
        </channel>
    </rss>
    """
    item_xml = ""
    for item in items:
        # Escape special chars for XML
        link = item.get('link', 'http://example.com').replace('&', '&amp;')

        item_xml += f"""
        <item>
            <title>{item.get('title', '-')}</title>
            <link>{link}</link>
            <description>{item.get('description', 'Test description')}</description>
            <guid>{item.get('guid', 'guid-123')}</guid>
            <pubDate>Mon, 01 Jan 2024 10:00:00 +0100</pubDate>
        </item>
        """
    return xml % item_xml

@patch("src.providers.oebb._fetch_xml")
@patch("src.providers.oebb.station_by_oebb_id")
@patch("src.providers.oebb.canonical_name")
def test_oebb_title_fallback_id(mock_canon, mock_station_lookup, mock_fetch):
    # Setup
    mock_station_lookup.return_value = "Wien ID-Station"
    mock_canon.side_effect = lambda x: x if x == "Wien ID-Station" else None

    # Item with poor title but valid ID in link
    # NOTE: In XML, & must be escaped as &amp;, but _extract_id_from_url will run on the parsed text content.
    # ET.fromstring parses &amp; back to &.
    items = [{
        "title": "-",
        "link": "http://fahrplan.oebb.at/bin/help.exe/dnl?protocol=https:&tpl=rss_WI_oebb&123456",
        "description": "Some disruption.",
        "guid": "guid-1"
    }]

    # Mock XML parsing
    root = ET.fromstring(mock_xml_response(items))
    mock_fetch.return_value = root

    # Run
    events = fetch_events()

    # Verify
    assert len(events) == 1
    assert events[0]["title"] == "Wien ID-Station"
    # Ensure ID extraction works (123456)
    mock_station_lookup.assert_called_with(123456)

@patch("src.providers.oebb._fetch_xml")
@patch("src.providers.oebb.station_by_oebb_id")
@patch("src.providers.oebb.canonical_name")
def test_oebb_title_fallback_text(mock_canon, mock_station_lookup, mock_fetch):
    # Setup
    mock_station_lookup.return_value = None # No ID match

    # Mock canonical_name to recognize "Text-Station"
    def fake_canon(name):
        if "Text-Station" in name:
            return "Text-Station"
        return None
    mock_canon.side_effect = fake_canon

    # Item with poor title, no ID, but text contains station
    # Ensure description contains "Wien" to pass strict filtering
    items = [{
        "title": "-",
        "link": "http://fahrplan.oebb.at/no-id",
        "description": "Bauarbeiten in Text-Station (Wien) wegen Wartung.",
        "guid": "guid-2"
    }]

    root = ET.fromstring(mock_xml_response(items))
    mock_fetch.return_value = root

    events = fetch_events()

    assert len(events) == 1
    assert events[0]["title"] == "Text-Station"

@patch("src.providers.oebb._fetch_xml")
@patch("src.providers.oebb.station_by_oebb_id")
@patch("src.providers.oebb.canonical_name")
def test_oebb_title_fallback_truncation(mock_canon, mock_station_lookup, mock_fetch):
    mock_station_lookup.return_value = None
    mock_canon.return_value = None # No stations found in text

    long_desc = "Wien: This is a very long description that definitely exceeds the limit of forty characters I assume."
    items = [{
        "title": "-",
        "link": "http://fahrplan.oebb.at/no-id",
        "description": long_desc,
        "guid": "guid-3"
    }]

    root = ET.fromstring(mock_xml_response(items))
    mock_fetch.return_value = root

    events = fetch_events()

    assert len(events) == 1
    # Check truncation (plain text)
    # The desc in item is stripped of HTML in real code, here it's already plain.
    # The truncation logic takes first 40 chars + ...
    expected = long_desc[:40] + "..."
    assert events[0]["title"] == expected
