
import unittest
from unittest.mock import MagicMock, patch
import requests
from src.utils.http import session_with_retries

class TestDNSRebindingBypass(unittest.TestCase):
    def test_dns_rebinding_bypass_prevented(self):
        """
        Verify that session.get() verifies the connected IP for the final response,
        preventing a DNS rebinding attack even if initial validation passes.
        """

        # We patch validate_http_url to always succeed (simulating that the DNS resolved to a safe IP initially)
        with patch("src.utils.http.validate_http_url", return_value="http://rebind.com"):

            # Mock the response to simulate a connection to an unsafe IP
            with patch('requests.adapters.HTTPAdapter.send') as mock_send:
                resp = requests.Response()
                resp.status_code = 200
                resp.url = "http://rebind.com"
                resp.raw = MagicMock()

                # Mock the socket to return a private IP
                mock_sock = MagicMock()
                mock_sock.getpeername.return_value = ('127.0.0.1', 80)
                resp.raw.connection.sock = mock_sock

                mock_send.return_value = resp

                session = session_with_retries("TestAgent")

                # We expect a ValueError because verify_response_ip should be called on the response
                # and detect the unsafe IP.
                with self.assertRaises(ValueError) as cm:
                    session.get("http://rebind.com")

                self.assertIn("Connected to unsafe IP", str(cm.exception))
                self.assertIn("127.0.0.1", str(cm.exception))

if __name__ == "__main__":
    unittest.main()
