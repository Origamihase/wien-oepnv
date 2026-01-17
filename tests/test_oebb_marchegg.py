import pytest
import re
from unittest.mock import MagicMock, patch
import xml.etree.ElementTree as ET
from providers.oebb import fetch_events, _is_relevant

# Mock XML content with the problematic title
MOCK_XML = """<?xml version="1.0" encoding="ISO-8859-1"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
<item>
<title>
<![CDATA[ Marchegg &lt; ↔ &gt; Bratislava hl.st. ]]>
</title>
<link>https://fahrplan.oebb.at/bin/help.exe/dn?L=vs_scotty&amp;tpl=showmap_external&amp;</link>
<guid isPermaLink="false">https://fahrplan.oebb.at/bin/query.exe/dn?ujm=1&amp;mapType=TRACKINFO&amp;846380</guid>
<pubDate>Wed, 14 Jan 2026 11:13:54 +0000</pubDate>
<description>
<![CDATA[ Wegen Bauarbeiten können zwischen Marchegg Bahnhof und Bratislava hl.st. von 04.05.2026 (07:50 Uhr) bis 08.05.2026 (16:00 Uhr) keine REX8-Züge …<br/>[04.05.2026 – 08.05.2026] ]]>
</description>
</item>
</channel>
</rss>
"""

class TestOebbMarchegg:
    """
    Reproduces the issue with 'Marchegg < ↔ > Bratislava hl.st.'.
    """

    @pytest.fixture
    def mock_fetch(self):
        with patch("providers.oebb._fetch_xml") as mock:
            mock.return_value = ET.fromstring(MOCK_XML)
            yield mock

    def test_fetch_events_formatting_and_filtering(self, mock_fetch):
        # Verify that fetch_events processes the item
        # Current behavior (buggy): Title has < >, and it might NOT be filtered (per user report).
        # Expected behavior (fixed): Title matches "Marchegg" (Outer) so it should be filtered.
        # AND if it were kept, it should be formatted nicely.

        # We force _is_relevant to return True momentarily to inspect the TITLE formatting
        # because if it works correctly, the item is filtered out and we can't see the title.
        with patch("providers.oebb._is_relevant", return_value=True) as mock_relevant:
            events = fetch_events()

            assert len(events) == 1
            title = events[0]["title"]

            # CHECK FORMATTING
            # If the bug is present, title is 'Marchegg < ↔ > Bratislava hl.st.' or 'Marchegg &lt; ↔ &gt; Bratislava hl.st.'
            # If fixed, it should be 'Marchegg ↔ Bratislava hl.st.'
            print(f"DEBUG: Parsed Title: '{title}'")

            # The user says "looks terrible", so we expect no < >.
            assert "<" not in title and ">" not in title, f"Title contains arrows: {title}"
            assert "&lt;" not in title, "Title contains encoded entity"
            assert "Marchegg ↔ Bratislava hl.st." in title

    def test_filtering_logic_marchegg(self):
        # Verify that _is_relevant correctly identifies this as irrelevant (Outer)
        # "Marchegg" is in data/stations.json as "in_vienna": false.

        # We need to simulate the title that would be passed to _is_relevant.
        # With the fix (unescape), title is "Marchegg ↔ Bratislava hl.st."
        # Without fix, it might be "Marchegg < ↔ > ..." or "Marchegg &lt; ↔ &gt; ..."

        # Test with FIXED title format
        title_fixed = "Marchegg ↔ Bratislava hl.st."
        desc = "Wegen Bauarbeiten können zwischen Marchegg Bahnhof und Bratislava hl.st. ..."

        # Marchegg is Outer, so it should be False.
        assert _is_relevant(title_fixed, desc) is False, "Marchegg (Outer) should be filtered out"

        # Test with BROKEN title format (current state)
        # Even with broken title, my manual reproduction showed it matches 'Marchegg'.
        title_broken = "Marchegg &lt; ↔ &gt; Bratislava hl.st."
        assert _is_relevant(title_broken, desc) is False, "Broken title should also be filtered (if regex matches)"
