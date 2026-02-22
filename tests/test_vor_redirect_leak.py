
import unittest
import requests
from src.providers import vor

class TestVorRedirectLeak(unittest.TestCase):
    def setUp(self):
        # Reset VOR_ACCESS_ID for testing
        vor.VOR_ACCESS_ID = "SECRET_TOKEN"
        vor._VOR_AUTHORIZATION_HEADER = ""

    def test_redirect_leaks_credentials(self):
        """
        Verify that VorAuth injects credentials only for VOR URLs and not for external URLs.
        Using VorAuth directly avoids mocking session internals which can be fragile.
        """
        auth = vor.VorAuth("SECRET_TOKEN", vor.VOR_BASE_URL)

        # 1. Test External URL (Attack Scenario)
        attacker_url = "http://attacker.com/steal"
        req = requests.PreparedRequest()
        req.url = attacker_url
        req.headers = {}

        # Apply auth
        auth(req)

        # Assert that the secret accessId IS NOT present
        self.assertNotIn("accessId", req.url, "Security Failure: accessId injected into external URL")

        # 2. Test Legit VOR URL (Functional Correctness)
        legit_url = vor.VOR_BASE_URL + "departureBoard"
        req = requests.PreparedRequest()
        req.url = legit_url
        req.headers = {}

        # Apply auth
        auth(req)

        self.assertIn("accessId=SECRET_TOKEN", req.url, "Functional Regression: accessId NOT injected into VOR URL")

if __name__ == "__main__":
    unittest.main()
