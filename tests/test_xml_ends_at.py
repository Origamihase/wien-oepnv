
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from src.build_feed import _make_rss

class TestXmlEndsAt(unittest.TestCase):
    def test_ends_at_xml_generation(self):
        # Setup
        now = datetime(2023, 10, 27, 10, 0, 0, tzinfo=timezone.utc)
        start = datetime(2023, 10, 27, 12, 0, 0, tzinfo=timezone.utc)
        end = datetime(2023, 10, 27, 14, 0, 0, tzinfo=timezone.utc)

        item = {
            "title": "Test Event",
            "description": "Test Description",
            "link": "https://example.com",
            "guid": "test-guid-123",
            "pubDate": now,
            "starts_at": start,
            "ends_at": end,
            "_identity": "test-guid-123",
            "source": "Test",
            "category": "TestCat"
        }

        # Execution
        rss_xml = _make_rss([item], now, {})

        # Verification
        root = ET.fromstring(rss_xml)
        channel = root.find("channel")
        rss_item = channel.find("item")

        ns = {"ext": "https://wien-oepnv.example/schema"}

        ends_at_elem = rss_item.find("ext:ends_at", ns)
        starts_at_elem = rss_item.find("ext:starts_at", ns)

        self.assertIsNotNone(ends_at_elem, "ext:ends_at element missing")
        self.assertIsNotNone(starts_at_elem, "ext:starts_at element missing")

        expected_end = "Fri, 27 Oct 2023 14:00:00 +0000"
        self.assertEqual(ends_at_elem.text, expected_end)

    def test_ends_at_missing_in_xml_when_none(self):
        now = datetime(2023, 10, 27, 10, 0, 0, tzinfo=timezone.utc)
        item = {
            "title": "Test Event No Ends",
            "description": "Test Description",
            "guid": "test-guid-456",
            "pubDate": now,
            "starts_at": now,
            "ends_at": None,
            "_identity": "test-guid-456",
            "source": "Test",
            "category": "TestCat"
        }

        rss_xml = _make_rss([item], now, {})
        root = ET.fromstring(rss_xml)
        rss_item = root.find("channel").find("item")
        ns = {"ext": "https://wien-oepnv.example/schema"}

        ends_at_elem = rss_item.find("ext:ends_at", ns)
        self.assertIsNone(ends_at_elem, "ext:ends_at should be missing when None")

if __name__ == "__main__":
    unittest.main()
