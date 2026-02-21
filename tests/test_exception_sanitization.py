import unittest
from unittest.mock import MagicMock, patch
import requests
import socket
from src.utils.http import session_with_retries, request_safe

class TestExceptionSanitization(unittest.TestCase):
    def test_request_safe_sanitizes_exception_message(self):
        """
        Verify that request_safe sanitizes secrets from exception messages.
        """
        safe_ip = "8.8.8.8"
        mock_addr_info = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (safe_ip, 80))]

        with patch('src.utils.http._resolve_hostname_safe', return_value=mock_addr_info):
            with patch('requests.Session.request') as mock_request:
                # Simulate an exception with a sensitive URL
                sensitive_url = "https://api.example.com/resource?token=SUPER_SECRET_KEY&user=admin"
                error_msg = f"ConnectionError: HTTPSConnectionPool(host='api.example.com', port=443): Max retries exceeded with url: {sensitive_url} (Caused by NewConnectionError('<urllib3.connection.HTTPSConnection object at ...>: Failed to establish a new connection: [Errno 111] Connection refused'))"

                mock_request.side_effect = requests.RequestException(error_msg)

                session = session_with_retries("TestAgent")

                with self.assertRaises(requests.RequestException) as cm:
                    request_safe(
                        session,
                        "https://api.example.com/resource?token=SUPER_SECRET_KEY&user=admin",
                    )

                exc_msg = str(cm.exception)

                # Check that secret is NOT in the message
                self.assertNotIn("SUPER_SECRET_KEY", exc_msg)

                # Check that it WAS redacted (accept encoded or unencoded ***)
                # %2A is *
                self.assertTrue("token=***" in exc_msg or "token=%2A%2A%2A" in exc_msg)

                # Check that non-sensitive parts are preserved
                self.assertIn("user=admin", exc_msg)
                self.assertIn("ConnectionError", exc_msg)

if __name__ == "__main__":
    unittest.main()
