import unittest
from unittest.mock import MagicMock, patch
import requests
import socket
from src.utils.http import session_with_retries, request_safe

class TestHTTPRequestSafe(unittest.TestCase):
    def test_request_safe_post_redirect_method_handling(self):
        """
        Verify that request_safe handles POST redirects correctly (switching to GET for 302, staying POST for 307).
        And ensures data is dropped when switching to GET.
        """
        safe_ip = "8.8.8.8"

        # Mock getaddrinfo to always return safe IP
        mock_addr_info = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (safe_ip, 80))]

        with patch('src.utils.http._resolve_hostname_safe', return_value=mock_addr_info):
            # We mock session.request to control responses directly
            with patch('requests.Session.request') as mock_request:

                # Response 1: 302 Found (POST -> GET)
                resp1 = requests.Response()
                resp1.status_code = 302
                resp1.headers['Location'] = '/redirected'
                resp1.url = 'http://example.com/login'
                resp1.raw = MagicMock()
                # Mock socket connection for verify_response_ip
                resp1.raw.connection.sock.getpeername.return_value = (safe_ip, 80)

                # Response 2: 200 OK (GET)
                resp2 = requests.Response()
                resp2.status_code = 200
                resp2._content = b"Success"
                # Mock iter_content to simulate streaming response
                resp2.iter_content = MagicMock(return_value=[b"Success"])
                resp2.url = 'http://example.com/redirected'
                resp2.headers['Content-Type'] = 'text/plain'
                resp2.raw = MagicMock()
                resp2.raw.connection.sock.getpeername.return_value = (safe_ip, 80)

                mock_request.side_effect = [resp1, resp2]

                session = session_with_retries("TestAgent")

                # Perform POST request
                response = request_safe(
                    session,
                    "http://example.com/login",
                    method="POST",
                    json={"user": "admin"}
                )

                self.assertEqual(response.content, b"Success")
                self.assertEqual(mock_request.call_count, 2)

                # Check first request (POST)
                args1, kwargs1 = mock_request.call_args_list[0]
                self.assertEqual(args1[0], "POST")
                self.assertIn(safe_ip, args1[1]) # Pinned IP in URL
                self.assertEqual(kwargs1['json'], {"user": "admin"})
                self.assertFalse(kwargs1['allow_redirects']) # Explicitly disabled

                # Check second request (GET)
                args2, kwargs2 = mock_request.call_args_list[1]
                self.assertEqual(args2[0], "GET") # Switched to GET
                self.assertIn(safe_ip, args2[1]) # Pinned IP
                self.assertIsNone(kwargs2.get('json')) # Data dropped
                self.assertFalse(kwargs2['allow_redirects']) # Explicitly disabled

    def test_request_safe_post_redirect_307_preserves_method(self):
        """
        Verify that 307 Temporary Redirect preserves POST method and data.
        """
        safe_ip = "8.8.8.8"
        mock_addr_info = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (safe_ip, 80))]

        with patch('src.utils.http._resolve_hostname_safe', return_value=mock_addr_info):
            with patch('requests.Session.request') as mock_request:

                # Response 1: 307 Temporary Redirect
                resp1 = requests.Response()
                resp1.status_code = 307
                resp1.headers['Location'] = '/redirected'
                resp1.url = 'http://example.com/login'
                resp1.raw = MagicMock()
                resp1.raw.connection.sock.getpeername.return_value = (safe_ip, 80)

                # Response 2: 200 OK (POST)
                resp2 = requests.Response()
                resp2.status_code = 200
                resp2._content = b"Success"
                resp2.iter_content = MagicMock(return_value=[b"Success"])
                resp2.url = 'http://example.com/redirected'
                resp2.headers['Content-Type'] = 'text/plain'
                resp2.raw = MagicMock()
                resp2.raw.connection.sock.getpeername.return_value = (safe_ip, 80)

                mock_request.side_effect = [resp1, resp2]

                session = session_with_retries("TestAgent")

                request_safe(
                    session,
                    "http://example.com/login",
                    method="POST",
                    json={"user": "admin"}
                )

                self.assertEqual(mock_request.call_count, 2)

                # Check second request (POST preserved)
                args2, kwargs2 = mock_request.call_args_list[1]
                self.assertEqual(args2[0], "POST")
                self.assertEqual(kwargs2['json'], {"user": "admin"}) # Data preserved

if __name__ == "__main__":
    unittest.main()
