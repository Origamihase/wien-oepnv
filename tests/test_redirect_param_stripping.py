
import unittest
from unittest.mock import MagicMock, patch
import requests
from src.utils.http import request_safe

class TestRedirectParamStripping(unittest.TestCase):
    @patch("src.utils.http._resolve_hostname_safe")
    @patch("src.utils.http.is_ip_safe")
    def test_sensitive_param_stripping_on_redirect(self, mock_is_ip_safe, mock_resolve):
        # Mock DNS resolution to be safe
        mock_resolve.return_value = [(2, 1, 6, '', ('93.184.216.34', 443))]
        mock_is_ip_safe.return_value = True

        session = MagicMock(spec=requests.Session)
        session.max_redirects = 5
        session.hooks = {"response": []}
        session.headers = {}

        # First response: 302 Redirect from trusted.com to evil.com with sensitive params
        resp1 = MagicMock(spec=requests.Response)
        resp1.status_code = 302
        resp1.is_redirect = True
        resp1.headers = {"Location": "https://evil.com/leak?accessId=SECRET123&token=SENSITIVE&public=ok"}
        resp1.url = "https://trusted.com/api"
        resp1.request = MagicMock()
        resp1.request.url = "https://trusted.com/api"
        # socket mock for verify_response_ip
        resp1.raw = MagicMock()
        resp1.raw.connection.sock.getpeername.return_value = ('93.184.216.34', 443)

        # Second response: 200 OK from evil.com
        resp2 = MagicMock(spec=requests.Response)
        resp2.status_code = 200
        resp2.is_redirect = False
        resp2.url = "https://evil.com/leak?public=ok" # Expected URL
        resp2.headers = {"Content-Type": "application/json"}
        resp2.iter_content.return_value = [b"{}"]
        resp2.raw = MagicMock()
        resp2.raw.connection.sock.getpeername.return_value = ('93.184.216.34', 443)

        # Mocking the context manager context
        cm1 = MagicMock()
        cm1.__enter__.return_value = resp1
        cm2 = MagicMock()
        cm2.__enter__.return_value = resp2

        session.request.side_effect = [cm1, cm2]

        try:
            request_safe(session, "https://trusted.com/api")
        except Exception:
            pass

        # Verify calls
        self.assertEqual(session.request.call_count, 2)
        args, kwargs = session.request.call_args_list[1]
        method, url = args

        # Check that sensitive params are stripped but public ones remain
        self.assertNotIn("accessId", url)
        self.assertNotIn("SECRET123", url)
        self.assertNotIn("token", url)
        self.assertNotIn("SENSITIVE", url)
        self.assertIn("public=ok", url)

if __name__ == "__main__":
    unittest.main()
