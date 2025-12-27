
import unittest
from unittest.mock import MagicMock, patch
import requests
from src.utils.http import session_with_retries, fetch_content_safe, validate_http_url

class TestSSRFRedirect(unittest.TestCase):
    def test_redirect_to_private_ip_is_blocked(self):
        """Ensure that redirects to private IPs (e.g. localhost) are blocked by the security hook."""

        with patch('requests.adapters.HTTPAdapter.send') as mock_send:
            # Helper to create a mock socket with a safe IP
            def get_safe_socket():
                mock_sock = MagicMock()
                mock_sock.getpeername.return_value = ('8.8.8.8', 80)
                return mock_sock

            # Response 1: 302 Redirect to localhost
            resp1 = requests.Response()
            resp1.status_code = 302
            resp1.headers['Location'] = 'http://localhost:8080/secret'
            resp1.url = 'http://safe.com'
            resp1.raw = MagicMock()
            resp1.raw.connection.sock = get_safe_socket()

            # Response 2: 200 OK (The secret, should NOT be reached)
            resp2 = requests.Response()
            resp2.status_code = 200
            resp2._content = b"SECRET_DATA"
            resp2.url = 'http://localhost:8080/secret'
            resp2.raw = MagicMock()
            # Even though this shouldn't be reached, if it were, we'd want it to look valid to avoid confusion,
            # but the test asserts we fail before this.

            # Mock behavior: return redirect first, then secret
            mock_send.side_effect = [resp1, resp2]

            session = session_with_retries("TestAgent")

            # We expect a ValueError because validate_http_url returns None for localhost,
            # and our hook raises ValueError when validate_http_url fails.
            with self.assertRaises(ValueError) as cm:
                 fetch_content_safe(session, "http://safe.com")

            self.assertIn("Unsafe redirect", str(cm.exception))

if __name__ == "__main__":
    unittest.main()
