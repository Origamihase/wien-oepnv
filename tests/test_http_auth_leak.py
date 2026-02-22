
import pytest
import requests
from unittest.mock import patch, MagicMock
from src.utils.http import request_safe, session_with_retries

def test_auth_kwargs_leak_on_redirect():
    # Setup session
    s = session_with_retries("test-agent")

    # We want to verify that if we pass auth=('user', 'pass'),
    # the Authorization header is NOT sent to the redirect target if it's a different host.

    # Mocks
    target_ip = "93.184.216.34" # example.com

    with patch("src.utils.http.session_with_retries") as mock_session_ctor:
        # We need to mock the session.request method to capture what's being sent
        mock_session = MagicMock()
        mock_session.max_redirects = 10
        mock_session.hooks = {}

        # First call: Returns 302 Redirect
        resp1 = requests.Response()
        resp1.status_code = 302
        resp1.headers["Location"] = "http://evil.com/leak"
        resp1.url = "http://safe.com/start"
        # request_safe needs raw connection for verify_response_ip
        resp1.raw = MagicMock()
        resp1.raw.connection.sock.getpeername.return_value = (target_ip, 80)

        # Second call: Returns 200 OK
        resp2 = requests.Response()
        resp2.status_code = 200
        resp2.url = "http://evil.com/leak"
        resp2.raw = MagicMock()
        resp2.raw.connection.sock.getpeername.return_value = (target_ip, 80) # mocking same IP for simplicity of safety check
        resp2._content = b"ok"

        # We mock session.request to return these in sequence
        # Note: request_safe calls session.request inside a loop
        mock_session.request.side_effect = [MagicMock(__enter__=lambda x: resp1, __exit__=lambda x,y,z,w: None),
                                            MagicMock(__enter__=lambda x: resp2, __exit__=lambda x,y,z,w: None)]

        # We also need to mock _resolve_hostname_safe to allow the request to proceed
        with patch("src.utils.http._resolve_hostname_safe") as mock_resolve, \
             patch("src.utils.http.verify_response_ip") as mock_verify, \
             patch("src.utils.http.validate_http_url") as mock_validate, \
             patch("src.utils.http._pin_url_to_ip") as mock_pin:

            # Setup mocks
            mock_validate.side_effect = lambda url, **kwargs: url # valid
            mock_pin.side_effect = lambda url: (url, "hostname") # mock pinning
            mock_resolve.return_value = [(2, 1, 6, '', (target_ip, 80))]

            # Execute request_safe with auth kwargs
            try:
                request_safe(mock_session, "http://safe.com/start", auth=("user", "pass"))
            except Exception:
                pass

            # Verify calls
            assert mock_session.request.call_count == 2

            # Check arguments of the first call (safe.com)
            args1, kwargs1 = mock_session.request.call_args_list[0]
            assert kwargs1.get("auth") == ("user", "pass")

            # Check arguments of the second call (evil.com)
            args2, kwargs2 = mock_session.request.call_args_list[1]
            target_url = args1[1]

            # The Critical Check: auth should be removed or None for the redirect to evil.com
            # If it is still ("user", "pass"), requests will generate the header for evil.com
            print(f"Redirect kwargs: {kwargs2}")

            # request_safe reuses kwargs for subsequent requests.
            # If 'auth' is still in kwargs2, it's a leak.
            if "auth" in kwargs2 and kwargs2["auth"] is not None:
                pytest.fail(f"Auth credentials leaked to redirect target! kwargs['auth'] = {kwargs2['auth']}")
