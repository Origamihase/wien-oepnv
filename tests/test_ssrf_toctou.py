
import pytest
import requests
from unittest.mock import MagicMock, patch
from src.utils.http import fetch_content_safe

def test_fetch_content_safe_ssrf_bypass_on_error():
    # Setup
    url = "http://evil.com/secret"

    # 1. Mock DNS to pass initial validation (return a public IP)
    with patch("src.utils.http._resolve_hostname_safe") as mock_dns:
        # 93.184.216.34 is example.com (safe)
        mock_dns.return_value = [(2, 1, 6, '', ('93.184.216.34', 80))]

        # 2. Mock Session to return a response that simulates:
        #    - 404 Not Found (so raise_for_status() triggers)
        #    - Connected to 127.0.0.1 (DNS Rebinding happened)
        session = requests.Session()

        mock_response = MagicMock(spec=requests.Response)
        mock_response.status_code = 404
        mock_response.url = url
        mock_response.headers = {}
        mock_response.is_redirect = False
        # Make raise_for_status raise HTTPError
        def raise_for_status_side_effect():
            raise requests.exceptions.HTTPError("404 Client Error", response=mock_response)
        mock_response.raise_for_status.side_effect = raise_for_status_side_effect

        # Mock the socket connection to return localhost
        mock_socket = MagicMock()
        mock_socket.getpeername.return_value = ('127.0.0.1', 80)

        mock_connection = MagicMock()
        mock_connection.sock = mock_socket

        mock_raw = MagicMock()
        mock_raw.connection = mock_connection
        mock_response.raw = mock_raw

        # Make context manager return the response
        mock_response.__enter__.return_value = mock_response
        mock_response.__exit__.return_value = None

        with patch.object(session, "request", return_value=mock_response) as mock_req:

            # 3. Execute
            # We now rely on session hooks for SSRF protection.
            # Since we mock session.request, the hook is not executed by requests.
            # We verify that the security hook is correctly passed to session.request.

            try:
                fetch_content_safe(session, url)
            except requests.exceptions.HTTPError:
                # Expected since we mocked the response to raise HTTPError and hooks didn't run
                pass
            except ValueError:
                pass

            # Verify the hook was passed
            assert mock_req.called
            call_kwargs = mock_req.call_args[1]
            hooks = call_kwargs.get("hooks", {})
            response_hooks = hooks.get("response", [])
            from src.utils.http import _check_response_security

            if _check_response_security not in response_hooks:
                pytest.fail("VULNERABLE: Security hook not passed to session.request")

            print("\nSECURE: Security hook verified.")

if __name__ == "__main__":
    try:
        test_fetch_content_safe_ssrf_bypass_on_error()
        print("Verification successful.")
    except Exception as e:
        print(f"\nVerification failed: {e}")
        import sys
        sys.exit(1)
