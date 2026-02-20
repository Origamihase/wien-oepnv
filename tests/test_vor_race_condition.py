import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime
from zoneinfo import ZoneInfo
import src.providers.vor as vor

class TestVorRaceCondition(unittest.TestCase):
    def test_fetch_aborts_if_limit_reached_concurrently(self):
        """
        Verify that _fetch_departure_board_for_station checks the request limit
        (via load_request_count) BEFORE making the network request.
        """
        # Mock load_request_count to return a value exceeding the limit
        mock_load = MagicMock(return_value=(None, vor.MAX_REQUESTS_PER_DAY))

        # Mock save_request_count
        mock_save = MagicMock()

        # Mock fetch_content_safe to track if it gets called
        mock_fetch = MagicMock(return_value="{}")

        # Mock session context manager
        mock_session = MagicMock()
        mock_session.__enter__.return_value = mock_session
        mock_session.__exit__.return_value = None
        mock_session_factory = MagicMock(return_value=mock_session)

        with patch("src.providers.vor.load_request_count", mock_load), \
             patch("src.providers.vor.save_request_count", mock_save), \
             patch("src.providers.vor.fetch_content_safe", mock_fetch), \
             patch("src.providers.vor.session_with_retries", mock_session_factory), \
             patch("src.providers.vor.apply_authentication"):

            now = datetime.now(ZoneInfo("Europe/Vienna"))
            vor._fetch_departure_board_for_station("12345", now)

            # We expect load_request_count to be called
            mock_load.assert_called()

            # We expect fetch_content_safe to NOT be called (aborted due to limit)
            mock_fetch.assert_not_called()

            # We expect save_request_count to NOT be called (aborted)
            mock_save.assert_not_called()

if __name__ == "__main__":
    unittest.main()
