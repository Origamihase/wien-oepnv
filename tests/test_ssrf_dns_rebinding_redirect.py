
import unittest
from unittest.mock import MagicMock, patch
import requests
from src.utils.http import session_with_retries, fetch_content_safe

class TestSSRFRedirectRebinding(unittest.TestCase):
    def test_dns_rebinding_on_redirect_pinned_to_safe_ip(self):
        """
        Verify that if a redirect target resolves to an unsafe IP (DNS Rebinding) in a TOCTOU scenario,
        the request is pinned to the SAFE IP resolved during the check, preventing connection to the unsafe IP.
        """
        safe_ip = "8.8.8.8"
        unsafe_ip = "127.0.0.1"

        # Mock dns.resolver.Resolver.resolve
        # 1. Initial validation -> Safe
        # 2. Initial pinning -> Safe
        # 3. Redirect validation (TOCTOU: Check sees Safe) -> Safe

        # If pinned: The request uses the IP from step 3 (Safe).
        # If not pinned (vulnerable): requests resolves internally.
        # But we mock send, so requests doesn't resolve.
        # BUT we check the URL passed to send.



        import dns.exception

        def mock_resolve_logic(host, record_type, *args, **kwargs):
            if record_type == 'A':
                ans1 = MagicMock()
                ans1.address = safe_ip
                return [ans1]
            raise dns.resolver.NoAnswer()



        with patch('dns.resolver.Resolver.resolve', side_effect=mock_resolve_logic) as mock_resolve:
            with patch('requests.adapters.HTTPAdapter.send') as mock_send:

                # Setup responses
                # Response 1: 302 Redirect
                resp1 = requests.Response()
                resp1.status_code = 302
                resp1.headers['Location'] = '/secret'
                resp1.url = 'http://attacker.com/'

                # Mock connection for verify_response_ip (Safe)
                conn1 = MagicMock()
                conn1.sock.getpeername.return_value = (safe_ip, 80)
                resp1.raw = MagicMock()
                resp1.raw.connection = conn1
                resp1.raw._connection = conn1
                resp1.request = MagicMock()
                resp1.request.url = 'http://attacker.com/'

                # Response 2: 200 OK
                resp2 = requests.Response()
                resp2.status_code = 200
                resp2._content = b"SECRET"
                resp2.url = 'http://attacker.com/secret'

                # Mock connection (Safe - as we expect pinning to work)
                conn2 = MagicMock()
                conn2.sock.getpeername.return_value = (safe_ip, 80)
                resp2.raw = MagicMock()
                resp2.raw.connection = conn2
                resp2.raw._connection = conn2

                mock_send.side_effect = [resp1, resp2]

                session = session_with_retries("TestAgent")

                # It should succeed now because we pin to safe IP
                content = fetch_content_safe(session, "http://attacker.com/")

                print(f"DEBUG: mock_send.call_count: {mock_send.call_count}")
                print(f"DEBUG: mock_resolve.call_count: {mock_resolve.call_count}")

                # Assert we followed redirect
                self.assertEqual(mock_send.call_count, 2)

                # Verify second request was pinned to IP
                # call_args is (args, kwargs). First arg is PreparedRequest.
                req2 = mock_send.call_args_list[1][0][0]

                # URL should use IP, not hostname
                self.assertIn("8.8.8.8", req2.url)
                self.assertNotIn("attacker.com", req2.url) # URL should NOT have hostname

                # Host header should be preserved
                self.assertEqual(req2.headers['Host'], "attacker.com")

if __name__ == "__main__":
    unittest.main()
