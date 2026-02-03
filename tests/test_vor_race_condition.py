import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime
from zoneinfo import ZoneInfo
import src.providers.vor as vor

class TestVorRaceCondition(unittest.TestCase):
    def test_fetch_aborts_if_limit_reached_concurrently(self):
        """
        Verify that _fetch_departure_board_for_station checks the request limit
        atomically (via save_request_count) BEFORE making the network request.

        This simulates a scenario where another process increments the counter
        just before this request proceeds.
        """
        # Mock save_request_count to return a value exceeding the limit
        # This simulates that while we might have passed the pre-flight check,
        # the actual counter is now full (e.g. due to race condition).
        mock_save = MagicMock(return_value=vor.MAX_REQUESTS_PER_DAY + 1)

        # Mock fetch_content_safe to track if it gets called
        mock_fetch = MagicMock(return_value="{}")

        # Mock session context manager
        mock_session = MagicMock()
        mock_session.__enter__.return_value = mock_session
        mock_session.__exit__.return_value = None
        mock_session_factory = MagicMock(return_value=mock_session)

        with patch("src.providers.vor.save_request_count", mock_save), \
             patch("src.providers.vor.fetch_content_safe", mock_fetch), \
             patch("src.providers.vor.session_with_retries", mock_session_factory), \
             patch("src.providers.vor.apply_authentication"):

            now = datetime.now(ZoneInfo("Europe/Vienna"))
            vor._fetch_departure_board_for_station("12345", now)

            # If the fix is implemented, we expect save_request_count to be called...
            mock_save.assert_called_once()

            # ...AND fetch_content_safe to NOT be called (aborted due to limit)
            # Currently (before fix), this assertion will fail because fetch is called first.
            if mock_fetch.call_count > 0:
                self.fail("fetch_content_safe was called despite limit being reached! Race condition vulnerability present.")

            mock_fetch.assert_not_called()

if __name__ == "__main__":
    unittest.main()
