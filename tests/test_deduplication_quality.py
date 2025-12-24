import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock
import sys
import os

# Adjust path to import src
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from build_feed import _dedupe_items, _dedupe_key_for_item

class TestDeduplicationQuality(unittest.TestCase):
    """
    Tests determining the quality of the deduplication logic.
    Ensures that:
    1. Exact duplicates are removed.
    2. Updates (same GUID/Identity) replace older items.
    3. 'Better' items (longer description, more recent) are preferred.
    """

    def test_exact_duplicates(self):
        """Scenario: Two identical items."""
        item1 = {
            "guid": "123",
            "title": "A",
            "description": "Desc",
            "pubDate": datetime(2023, 1, 1, tzinfo=timezone.utc)
        }
        item2 = item1.copy()

        result = _dedupe_items([item1, item2])
        self.assertEqual(len(result), 1, "Should reduce exact duplicates to 1")

    def test_update_behavior_same_guid(self):
        """Scenario: Item updates with same GUID. New item has longer description."""
        item_old = {
            "guid": "123",
            "title": "Störung",
            "description": "Kurz",
            "pubDate": datetime(2023, 1, 1, 10, 0, tzinfo=timezone.utc),
            "_calculated_recency": datetime(2023, 1, 1, 10, 0, tzinfo=timezone.utc)
        }
        item_new = {
            "guid": "123",
            "title": "Störung Update",
            "description": "Kurz aber länger", # Longer description -> Better
            "pubDate": datetime(2023, 1, 1, 11, 0, tzinfo=timezone.utc),
            "_calculated_recency": datetime(2023, 1, 1, 11, 0, tzinfo=timezone.utc)
        }

        # Order shouldn't matter for correctness, but _dedupe_items iterates sequentially.
        # Case 1: Old then New
        result1 = _dedupe_items([item_old, item_new])
        self.assertEqual(len(result1), 1)
        self.assertEqual(result1[0]["description"], "Kurz aber länger", "Should keep the 'better' item (longer desc)")

        # Case 2: New then Old
        result2 = _dedupe_items([item_new, item_old])
        self.assertEqual(len(result2), 1)
        self.assertEqual(result2[0]["description"], "Kurz aber länger", "Should keep the 'better' item regardless of order")

    def test_update_behavior_later_end_date(self):
        """Scenario: Item updates with extended end time."""
        item_short = {
            "guid": "123",
            "title": "Störung",
            "ends_at": datetime(2023, 1, 1, 12, 0, tzinfo=timezone.utc)
        }
        item_long = {
            "guid": "123",
            "title": "Störung",
            "ends_at": datetime(2023, 1, 1, 14, 0, tzinfo=timezone.utc) # Later end -> Better
        }

        result = _dedupe_items([item_short, item_long])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["ends_at"], item_long["ends_at"], "Should prefer item with later end date")

    def test_fallback_identity_collision(self):
        """Scenario: No GUID, same content -> Deduplication (Good)."""
        item1 = {
            "source": "Src",
            "title": "Title",
            "description": "Same Content"
        }
        item2 = {
            "source": "Src",
            "title": "Title",
            "description": "Same Content"
        }

        # Verify keys are same
        k1, _ = _dedupe_key_for_item(item1)
        k2, _ = _dedupe_key_for_item(item2)
        self.assertEqual(k1, k2)

        result = _dedupe_items([item1, item2])
        self.assertEqual(len(result), 1)

    def test_fallback_identity_failure(self):
        """Scenario: No GUID, content update -> Duplication (Expected limitation)."""
        item1 = {
            "source": "Src",
            "title": "Title",
            "description": "Content A"
        }
        item2 = {
            "source": "Src",
            "title": "Title",
            "description": "Content A Update"
        }

        # Without GUID/_identity, these are treated as different items
        result = _dedupe_items([item1, item2])
        self.assertEqual(len(result), 2, "Updates without stable ID result in duplicates")

if __name__ == '__main__':
    unittest.main()
