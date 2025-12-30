from unittest.mock import MagicMock, patch
from src.providers import oebb
from defusedxml import ElementTree as ET

def test_oebb_title_fallback_strategy_a():
    """Test Strategy A: Use part before colon if title is poor."""
    # Create an XML structure mimicking ÖBB RSS
    xml_content = """
    <rss version="2.0">
        <channel>
            <item>
                <title>-</title>
                <link>http://example.com</link>
                <description>Station Silberwald: Aufzug außer Betrieb</description>
                <guid>123</guid>
                <pubDate>Mon, 01 Jan 2024 12:00:00 GMT</pubDate>
            </item>
        </channel>
    </rss>
    """
    root = ET.fromstring(xml_content)

    with patch("src.providers.oebb._fetch_xml", return_value=root) as mock_fetch:
        with patch("src.providers.oebb.is_in_vienna", return_value=True): # Ensure it passes region filter
             events = oebb.fetch_events()

    assert len(events) == 1
    # Should fallback to "Station Silberwald"
    assert events[0]["title"] == "Station Silberwald"
    assert events[0]["description"] == "Station Silberwald: Aufzug außer Betrieb"


def test_oebb_title_fallback_strategy_b():
    """Test Strategy B: Use first 40 chars if no colon and title is poor."""
    long_desc = "This is a very long description that definitely exceeds the forty character limit we have set."

    xml_content = f"""
    <rss version="2.0">
        <channel>
            <item>
                <title></title>
                <link>http://example.com</link>
                <description>{long_desc}</description>
                <guid>123</guid>
                <pubDate>Mon, 01 Jan 2024 12:00:00 GMT</pubDate>
            </item>
        </channel>
    </rss>
    """
    root = ET.fromstring(xml_content)

    with patch("src.providers.oebb._fetch_xml", return_value=root) as mock_fetch:
         with patch("src.providers.oebb.is_in_vienna", return_value=True):
             events = oebb.fetch_events()

    assert len(events) == 1
    # Should fallback to first 40 chars + "..."
    expected_title = long_desc[:40] + "..."
    assert events[0]["title"] == expected_title


def test_oebb_title_no_fallback_needed():
    """Ensure good titles are left alone."""
    xml_content = """
    <rss version="2.0">
        <channel>
            <item>
                <title>Wien Mitte ↔ Wien Praterstern</title>
                <link>http://example.com</link>
                <description>Some details</description>
                <guid>123</guid>
                <pubDate>Mon, 01 Jan 2024 12:00:00 GMT</pubDate>
            </item>
        </channel>
    </rss>
    """
    root = ET.fromstring(xml_content)

    with patch("src.providers.oebb._fetch_xml", return_value=root) as mock_fetch:
         with patch("src.providers.oebb.is_in_vienna", return_value=True):
             events = oebb.fetch_events()

    assert len(events) == 1
    assert events[0]["title"] == "Wien Mitte ↔ Wien Praterstern"

def test_oebb_title_fallback_strategy_b_short():
    """Test Strategy B with short description (no truncation needed if < 40 chars?).
       The requirement says 'use the first 40 characters... (truncated with "...")'.
       It doesn't explicitly say what to do if it's shorter than 40.
       Usually we don't add ellipsis if it's short.
       Let's assume we just take it all if < 40.
    """
    short_desc = "Short desc"

    xml_content = f"""
    <rss version="2.0">
        <channel>
            <item>
                <title>-</title>
                <link>http://example.com</link>
                <description>{short_desc}</description>
                <guid>123</guid>
                <pubDate>Mon, 01 Jan 2024 12:00:00 GMT</pubDate>
            </item>
        </channel>
    </rss>
    """
    root = ET.fromstring(xml_content)

    with patch("src.providers.oebb._fetch_xml", return_value=root) as mock_fetch:
         with patch("src.providers.oebb.is_in_vienna", return_value=True):
             events = oebb.fetch_events()

    assert len(events) == 1
    # Should fallback to "Short desc"
    # If logic is strict "first 40 chars... truncated with ..." even if short,
    # we'll see. But "truncated with" usually implies only if truncation happens.
    # However, I will implement it such that it handles short strings gracefully.
    assert events[0]["title"] == short_desc
