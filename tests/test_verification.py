
import unittest
from unittest.mock import patch, MagicMock, mock_open
import sys
import os
from datetime import datetime, timezone

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from build_feed import _dedupe_items, _save_state, _parse_datetime

class TestFixes(unittest.TestCase):

    def test_dedupe_logic(self):
        # Scenario: Two items, same end date.
        # Item A: More recent recency, shorter description.
        # Item B: Older recency, longer description.
        # Expected: A should win (Recency > Length).
        # Current (Buggy): B wins (Length > Recency).

        dt_end = datetime(2025, 1, 1, tzinfo=timezone.utc)
        dt_recent = datetime(2025, 1, 1, tzinfo=timezone.utc)
        dt_old = datetime(2024, 1, 1, tzinfo=timezone.utc)

        item_a = {
            "title": "Item A",
            "ends_at": dt_end,
            "pubDate": dt_recent,
            "description": "Short",
            "guid": "same_guid"
        }
        item_b = {
            "title": "Item B",
            "ends_at": dt_end,
            "pubDate": dt_old,
            "description": "Longer Description Here",
            "guid": "same_guid"
        }

        # We need to ensure _dedupe_items uses our custom order logic.
        # It sorts internally or iterates. The implementation uses a dict seen[key] = index.
        # If key exists, it calls _better(new_item, existing_item).

        # Order 1: A then B.
        # seen[key] = A.
        # check _better(B, A).
        # If B is better, replace A.
        # We want A to be better. So _better(B, A) should be False.
        # If buggy, B (longer) > A (shorter) -> True. Replace A with B. Result B.

        # Order 2: B then A.
        # seen[key] = B.
        # check _better(A, B).
        # We want A to be better. So _better(A, B) should be True.
        # If buggy, A (shorter) < B (longer) -> False. Keep B. Result B.

        # Test Order 1
        result1 = _dedupe_items([item_a, item_b])
        self.assertEqual(result1[0]["title"], "Item A", "Item A should win due to recency (Order A, B)")

        # Test Order 2
        result2 = _dedupe_items([item_b, item_a])
        self.assertEqual(result2[0]["title"], "Item A", "Item A should win due to recency (Order B, A)")

    @patch("build_feed.validate_path")
    @patch("build_feed._file_lock")
    @patch("build_feed.atomic_write")
    @patch("json.dump")
    @patch("json.load")
    @patch("pathlib.Path.open", new_callable=mock_open)
    def test_save_state_merge(self, mock_file, mock_load, mock_dump, mock_atomic, mock_lock, mock_validate):
        # Setup mocks
        mock_path_obj = MagicMock()
        mock_path_obj.parent.mkdir.return_value = None
        mock_path_obj.with_suffix.return_value = MagicMock() # lock path
        mock_validate.return_value = mock_path_obj

        # Current state on disk
        existing_state = {
            "id1": {"first_seen": "2024-01-01T00:00:00+00:00", "other": "data"}
        }
        mock_load.return_value = existing_state

        # New state to save
        new_state = {
            "id2": {"first_seen": "2024-01-02T00:00:00+00:00"}
        }

        # Call _save_state
        # Note: In the current implementation, _save_state DOES NOT read the file.
        # It just dumps `new_state`.
        # So mock_load won't be called, and json.dump will receive only new_state.

        # We want to assert that AFTER the fix, existing_state is merged.

        try:
            _save_state(new_state)
        except Exception as e:
            # Might fail if implementation differs significantly from assumption
            print(f"Save state failed: {e}")
            pass

        # Check what was dumped
        # args[0] is the data
        if mock_dump.call_args:
            dumped_data = mock_dump.call_args[0][0]
            # Expectation for FIX: id1 should be present.
            # Expectation for BUG: id1 is missing.

            if "id1" in dumped_data:
                print("Merge SUCCESS (Fixed behavior)")
            else:
                print("Merge FAIL (Buggy behavior - Lost Update)")
                # We assert failure to confirm reproduction of bug
                self.fail("State was overwritten without merging! (Expected failure before fix)")

    def test_date_parsing_z(self):
        # This will likely pass on Python 3.12, but we verify it works.
        dt = _parse_datetime("2023-01-01T12:00:00Z")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.year, 2023)

if __name__ == "__main__":
    unittest.main()
