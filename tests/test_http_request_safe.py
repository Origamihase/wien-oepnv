import unittest
from unittest.mock import MagicMock, patch
import requests
import socket
from src.utils.http import session_with_retries, request_safe

class TestHTTPRequestSafe(unittest.TestCase):
    def test_request_safe_post_redirect_method_handling(self) -> None:
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
                conn = MagicMock()
                conn.sock.getpeername.return_value = (safe_ip, 80)
                resp1.raw.connection = conn
                resp1.raw._connection = conn

                # Response 2: 200 OK (GET)
                resp2 = requests.Response()
                resp2.status_code = 200
                resp2._content = b"Success"
                # Mock iter_content to simulate streaming response
                resp2.iter_content = MagicMock(return_value=[b"Success"])
                resp2.url = 'http://example.com/redirected'
                resp2.headers['Content-Type'] = 'text/plain'
                resp2.raw = MagicMock()
                conn2 = MagicMock()
                conn2.sock.getpeername.return_value = (safe_ip, 80)
                resp2.raw.connection = conn2
                resp2.raw._connection = conn2

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

    def test_request_safe_post_redirect_307_preserves_method(self) -> None:
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
                conn = MagicMock()
                conn.sock.getpeername.return_value = (safe_ip, 80)
                resp1.raw.connection = conn
                resp1.raw._connection = conn

                # Response 2: 200 OK (POST)
                resp2 = requests.Response()
                resp2.status_code = 200
                resp2._content = b"Success"
                resp2.iter_content = MagicMock(return_value=[b"Success"])
                resp2.url = 'http://example.com/redirected'
                resp2.headers['Content-Type'] = 'text/plain'
                resp2.raw = MagicMock()
                conn2 = MagicMock()
                conn2.sock.getpeername.return_value = (safe_ip, 80)
                resp2.raw.connection = conn2
                resp2.raw._connection = conn2

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

    def test_request_safe_security_hook_always_attached(self) -> None:
        """
        Regression: ``_check_response_security`` MUST always be present in the
        response hooks for every outgoing request. If accidentally dropped,
        IP-verification (DNS-rebinding TOCTOU defense) would silently no-op.
        """
        from src.utils.http import _check_response_security
        safe_ip = "8.8.8.8"
        mock_addr_info = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (safe_ip, 80))]

        with patch('src.utils.http._resolve_hostname_safe', return_value=mock_addr_info):
            with patch('requests.Session.request') as mock_request:
                resp = requests.Response()
                resp.status_code = 200
                resp._content = b"ok"
                resp.iter_content = MagicMock(return_value=[b"ok"])
                resp.url = 'http://example.com/'
                resp.headers['Content-Type'] = 'text/plain'
                resp.raw = MagicMock()
                conn = MagicMock()
                conn.sock.getpeername.return_value = (safe_ip, 80)
                resp.raw.connection = conn
                resp.raw._connection = conn

                mock_request.return_value = resp

                session = session_with_retries("TestAgent")
                request_safe(session, "http://example.com/")

                _, kwargs = mock_request.call_args
                hooks = kwargs.get("hooks", {})
                response_hooks = hooks.get("response", [])
                if not isinstance(response_hooks, list):
                    response_hooks = [response_hooks]
                self.assertIn(
                    _check_response_security,
                    response_hooks,
                    "_check_response_security MUST always be in response hooks",
                )

    def test_request_safe_caller_hooks_preserved(self) -> None:
        """
        Regression: caller-passed hooks MUST coexist with the security hook;
        merging must not drop or replace either side. Verifies a custom
        response hook supplied by the caller still fires AND the security
        hook is still present.
        """
        from src.utils.http import _check_response_security
        safe_ip = "8.8.8.8"
        mock_addr_info = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (safe_ip, 80))]

        custom_hook_calls: list[str] = []
        def custom_hook(response: requests.Response, *args: object, **kwargs: object) -> None:
            custom_hook_calls.append("called")

        with patch('src.utils.http._resolve_hostname_safe', return_value=mock_addr_info):
            with patch('requests.Session.request') as mock_request:
                resp = requests.Response()
                resp.status_code = 200
                resp._content = b"ok"
                resp.iter_content = MagicMock(return_value=[b"ok"])
                resp.url = 'http://example.com/'
                resp.headers['Content-Type'] = 'text/plain'
                resp.raw = MagicMock()
                conn = MagicMock()
                conn.sock.getpeername.return_value = (safe_ip, 80)
                resp.raw.connection = conn
                resp.raw._connection = conn

                mock_request.return_value = resp

                session = session_with_retries("TestAgent")
                request_safe(
                    session,
                    "http://example.com/",
                    hooks={"response": custom_hook},
                )

                _, kwargs = mock_request.call_args
                hooks = kwargs.get("hooks", {})
                response_hooks = hooks.get("response", [])
                if not isinstance(response_hooks, list):
                    response_hooks = [response_hooks]
                self.assertIn(custom_hook, response_hooks, "Caller hook must survive merge")
                self.assertIn(
                    _check_response_security,
                    response_hooks,
                    "Security hook must coexist with caller hooks",
                )

    def test_request_safe_tuple_timeout_total_budget_sums(self) -> None:
        """
        Regression: when ``timeout=(connect, read)`` is given, the total time
        budget across redirects MUST be the SUM of both values (not just
        ``connect`` or just ``read``). If summing is dropped, a slow
        adversary could chain redirects and stretch the budget unboundedly.
        """
        safe_ip = "8.8.8.8"
        mock_addr_info = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (safe_ip, 80))]

        with patch('src.utils.http._resolve_hostname_safe', return_value=mock_addr_info):
            # We freeze monotonic so we can control elapsed time precisely.
            # Sequence of monotonic reads inside request_safe:
            #   (1) start_time = time.monotonic()           — t=0
            #   (2) elapsed = time.monotonic() - start_time — first iter, after 25s
            # Total budget for tuple (3, 15) is 18; elapsed=25 > 18 -> Timeout.
            with patch('src.utils.http.time.monotonic') as mock_monotonic:
                mock_monotonic.side_effect = [0.0, 25.0, 25.0, 25.0, 25.0]

                with patch('requests.Session.request') as mock_request:
                    session = session_with_retries("TestAgent")
                    with self.assertRaises(requests.Timeout) as cm:
                        request_safe(
                            session,
                            "http://example.com/",
                            timeout=(3.0, 15.0),
                        )
                    # The error should reference the SUMMED budget (18s), not 3s or 15s.
                    self.assertIn("18", str(cm.exception))
                    # And no actual HTTP request was made because the budget tripped first.
                    self.assertEqual(mock_request.call_count, 0)


if __name__ == "__main__":
    unittest.main()
