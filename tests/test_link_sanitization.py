
import pytest
import datetime
import xml.etree.ElementTree as ET
from src import build_feed
from src.feed.config import FEED_LINK

def _emit_item_str(item, now, state):
    ident, elem, replacements = build_feed._emit_item(item, now, state)
    xml_str = ET.tostring(elem, encoding="unicode")
    for ph, content in replacements.items():
        xml_str = xml_str.replace(ph, content)
    return ident, xml_str

def test_javascript_link_sanitization():
    # Mock date and state
    now = datetime.datetime(2025, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
    state = {}

    # Item with malicious javascript link
    item = {
        "title": "Test Item",
        "link": "javascript:alert('XSS')",
        "guid": "test-guid-1",
        "pubDate": now,
        "description": "Description"
    }

    # Execute
    ident, xml = _emit_item_str(item, now, state)

    # Verify
    assert "javascript:alert" not in xml
    # Should fall back to FEED_LINK or be empty (if FEED_LINK is empty)
    # FEED_LINK defaults to https://github.com/Origamihase/wien-oepnv in config but might vary
    # We check that <link> content is NOT the malicious one.
    assert f"<link>{item['link']}</link>" not in xml

    # It should match FEED_LINK if FEED_LINK is set
    if FEED_LINK:
        assert f"<link>{FEED_LINK}</link>" in xml

def test_valid_http_link_preserved():
    now = datetime.datetime(2025, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
    state = {}

    valid_link = "https://example.com/article"
    item = {
        "title": "Test Item",
        "link": valid_link,
        "guid": "test-guid-2",
        "pubDate": now,
        "description": "Description"
    }

    ident, xml = _emit_item_str(item, now, state)

    assert f"<link>{valid_link}</link>" in xml
