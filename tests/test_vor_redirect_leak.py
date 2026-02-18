
import unittest
from unittest.mock import MagicMock, patch
import requests
from src.providers import vor

class TestVorRedirectLeak(unittest.TestCase):
    def setUp(self):
        # Reset VOR_ACCESS_ID for testing
        vor.VOR_ACCESS_ID = "SECRET_TOKEN"
        vor._VOR_AUTHORIZATION_HEADER = ""

    @patch("src.providers.vor.refresh_access_credentials")
    def test_redirect_leaks_credentials(self, mock_refresh):
        # Ensure refresh_access_credentials does nothing or returns our token
        vor.VOR_ACCESS_ID = "SECRET_TOKEN"
        vor._VOR_AUTHORIZATION_HEADER = ""
        mock_refresh.return_value = "SECRET_TOKEN"

        session = requests.Session()

        # Mock the original request method BEFORE apply_authentication wraps it
        original_request = MagicMock()
        session.request = original_request

        # Apply the authentication wrapper
        vor.apply_authentication(session)

        # 1. Test External URL (Attack Scenario)
        attacker_url = "http://attacker.com/steal"
        session.request("GET", attacker_url)

        self.assertTrue(original_request.called)
        call_args = original_request.call_args
        _, kwargs = call_args
        params = kwargs.get("params")
        if params is None:
            params = {}

        # Assert that the secret accessId IS NOT present
        self.assertNotIn("accessId", params, "Security Failure: accessId injected into external URL")

        # 2. Test Legit VOR URL (Functional Correctness)
        original_request.reset_mock()
        legit_url = vor.VOR_BASE_URL + "departureBoard"
        session.request("GET", legit_url)

        self.assertTrue(original_request.called)
        call_args = original_request.call_args
        _, kwargs = call_args
        params = kwargs.get("params")
        if params is None:
            params = {}

        self.assertIn("accessId", params, "Functional Regression: accessId NOT injected into VOR URL")
        self.assertEqual(params["accessId"], "SECRET_TOKEN")

if __name__ == "__main__":
    unittest.main()
