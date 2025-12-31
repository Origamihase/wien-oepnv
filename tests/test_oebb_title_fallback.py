from unittest.mock import MagicMock, patch
from src.providers import oebb
from defusedxml import ElementTree as ET

# --- Helpers to mock data ---

def mock_station_entries():
    return (
        {"bst_id": 123456, "name": "ID-Station", "in_vienna": True, "pendler": False},
        {"bst_id": 999999, "name": "Another Station", "in_vienna": True, "pendler": False},
    )

def mock_canonical_name(name):
    if "Text-Station" in name:
        return "Text-Station"
    if "Station A" in name:
        return "Station A"
    if "Station B" in name:
        return "Station B"
    return None

def mock_is_in_vienna(name):
    return True

# --- Tests ---

def test_oebb_fallback_attempt_1_id_match():
    """Attempt 1: Title from Station ID in link."""
    xml_content = """
    <rss version="2.0">
        <channel>
            <item>
                <title>-</title>
                <link>http://example.com?foo=bar&amp;station=123456</link>
                <description>Some generic description</description>
                <guid>GUID-123456</guid>
                <pubDate>Mon, 01 Jan 2024 12:00:00 GMT</pubDate>
            </item>
        </channel>
    </rss>
    """
    root = ET.fromstring(xml_content)

    with patch("src.providers.oebb._fetch_xml", return_value=root), \
         patch("src.providers.oebb._station_entries", side_effect=mock_station_entries), \
         patch("src.providers.oebb.is_in_vienna", side_effect=mock_is_in_vienna):

        events = oebb.fetch_events()

    assert len(events) == 1
    assert events[0]["title"] == "ID-Station"

def test_oebb_fallback_attempt_2_text_match_single():
    """Attempt 2: One station found in text."""
    xml_content = """
    <rss version="2.0">
        <channel>
            <item>
                <title></title>
                <link>http://example.com</link>
                <description>Bauarbeiten in Text-Station wegen Wartung.</description>
                <guid>GUID-NOID</guid>
                <pubDate>Mon, 01 Jan 2024 12:00:00 GMT</pubDate>
            </item>
        </channel>
    </rss>
    """
    root = ET.fromstring(xml_content)

    with patch("src.providers.oebb._fetch_xml", return_value=root), \
         patch("src.providers.oebb._station_entries", side_effect=mock_station_entries), \
         patch("src.providers.oebb.canonical_name", side_effect=mock_canonical_name), \
         patch("src.providers.oebb.is_in_vienna", side_effect=mock_is_in_vienna):

        events = oebb.fetch_events()

    assert len(events) == 1
    assert events[0]["title"] == "Text-Station"

def test_oebb_fallback_attempt_2_text_match_multiple():
    """Attempt 2: Multiple stations found in text."""
    xml_content = """
    <rss version="2.0">
        <channel>
            <item>
                <title>-</title>
                <link>http://example.com</link>
                <description>Schienenersatzverkehr zwischen Station A und Station B eingerichtet.</description>
                <guid>GUID-NOID</guid>
                <pubDate>Mon, 01 Jan 2024 12:00:00 GMT</pubDate>
            </item>
        </channel>
    </rss>
    """
    root = ET.fromstring(xml_content)

    with patch("src.providers.oebb._fetch_xml", return_value=root), \
         patch("src.providers.oebb._station_entries", side_effect=mock_station_entries), \
         patch("src.providers.oebb.canonical_name", side_effect=mock_canonical_name), \
         patch("src.providers.oebb.is_in_vienna", side_effect=mock_is_in_vienna):

        events = oebb.fetch_events()

    assert len(events) == 1
    # Should combine them
    assert events[0]["title"] == "Station A ↔ Station B"

def test_oebb_fallback_attempt_3_truncation():
    """Attempt 3: No ID, no Station Text -> Truncate."""
    # Note: The colon strategy is actually part of "emergency" fallback in the prompt requirements?
    # The prompt says: "Attempt 3 (Emergency): Fallback to truncation (first 40 chars of description)."
    # Wait, the previous task had "Strategy A (Colon)" and "Strategy B (Truncate)".
    # The new prompt effectively overrides the previous requirements but mentions "Attempt 3".
    # I should check if the user implicitly wants to keep the colon logic or replace it entirely with just truncation.
    # The new prompt says: "Attempt 3 (Emergency): Fallback to truncation".
    # However, usually we should preserve useful heuristics. The colon strategy is very useful.
    # But strictly following "Requirements":
    # 1. ID
    # 2. Text Search
    # 3. Truncation
    # It doesn't mention Colon strategy anymore.
    # I will stick to what the prompt explicitly requested for this task.
    # But wait, looking at the previous memory, the user said "Robustly fix...".
    # If I drop the colon strategy, I might degrade quality for "Station: Details" cases where "Station" is not in the DB.
    # Let's see if I can integrate it.
    # Actually, the prompt says "Attempt 3 (Emergency): Fallback to truncation".
    # I will interpret this as the final catch-all.
    # If I can find a "station name" via text search, good.
    # If the "Station: Details" pattern exists, the text search for "Station" might find it if it is a known station.
    # If "Station" is unknown, then maybe the colon strategy is still better than truncation?
    # I will stick to the plan but maybe keep colon if it falls through to truncation logic (as a refinement of truncation).
    # Re-reading prompt: "Attempt 3 (Emergency): Fallback to truncation (first 40 chars of description)."
    # I will implement exactly that. If the colon strategy was deemed "poor" or "insufficient", this replaces it.

    long_desc = "Just some random text that is definitely longer than forty characters."
    xml_content = f"""
    <rss version="2.0">
        <channel>
            <item>
                <title>-</title>
                <link>http://example.com</link>
                <description>{long_desc}</description>
                <guid>GUID-NOID</guid>
                <pubDate>Mon, 01 Jan 2024 12:00:00 GMT</pubDate>
            </item>
        </channel>
    </rss>
    """
    root = ET.fromstring(xml_content)

    with patch("src.providers.oebb._fetch_xml", return_value=root), \
         patch("src.providers.oebb._station_entries", side_effect=mock_station_entries), \
         patch("src.providers.oebb.canonical_name", return_value=None), \
         patch("src.providers.oebb.is_in_vienna", side_effect=mock_is_in_vienna):

        events = oebb.fetch_events()

    assert len(events) == 1
    assert events[0]["title"] == long_desc[:40] + "..."
