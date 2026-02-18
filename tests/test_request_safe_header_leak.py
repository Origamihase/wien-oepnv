import pytest
import requests
import responses
from unittest.mock import patch, MagicMock
from src.utils.http import request_safe, session_with_retries

@responses.activate
def test_request_safe_strips_session_headers_on_redirect():
    # Setup
    s = session_with_retries("test-agent")
    s.headers["Authorization"] = "Bearer session-secret"
    s.headers["X-Custom-Secret"] = "super-secret"

    # We force the IP to be constant
    target_ip = "93.184.216.34"

    # Mock redirect - registered on the IP!
    responses.add(
        responses.GET,
        f"http://{target_ip}/start",
        headers={"Location": "http://evil.com/leak"},
        status=302
    )

    responses.add(
        responses.GET,
        f"http://{target_ip}/leak",
        body="ok",
        status=200
    )

    # Mock DNS resolution to return safe IP
    with patch("src.utils.http._resolve_hostname_safe") as mock_resolve:
        # Return a safe IP for any hostname
        mock_resolve.return_value = [
            (2, 1, 6, '', (target_ip, 80))
        ]

        # Mock verify_response_ip to pass
        with patch("src.utils.http.verify_response_ip") as mock_verify:
             # Execute
            try:
                request_safe(s, "http://safe.com/start")
            except Exception as e:
                print(f"Caught exception: {e}")
                pass

    # Verify
    assert len(responses.calls) == 2

    # Call 1: safe.com - should have headers
    assert responses.calls[0].request.headers["Authorization"] == "Bearer session-secret"
    assert responses.calls[0].request.headers["X-Custom-Secret"] == "super-secret"

    # Call 2: evil.com - should NOT have sensitive headers
    headers_sent = responses.calls[1].request.headers

    print(f"Auth header sent: {headers_sent.get('Authorization')}")
    print(f"Custom Secret sent: {headers_sent.get('X-Custom-Secret')}")

    assert responses.calls[1].request.headers["Host"] == "evil.com"

    assert "Authorization" not in headers_sent, "Authorization header leaked!"
    assert "X-Custom-Secret" not in headers_sent, "Custom Secret header leaked!"
