import pytest
import responses
from unittest.mock import patch
from src.utils.http import session_with_retries

@patch("src.utils.http.verify_response_ip")
@patch("src.utils.http.validate_http_url")
def test_strip_dynamic_sensitive_headers(mock_validate_url, mock_verify_ip):
    """Verify that dynamically detected sensitive headers are stripped on cross-origin redirects."""
    mock_validate_url.side_effect = lambda url, **kwargs: url
    mock_verify_ip.return_value = None

    session = session_with_retries("test-agent")

    @responses.activate
    def run():
        # Setup redirect: host1 -> host2
        responses.add(responses.GET, "https://api.example.com/", status=302, headers={"Location": "https://malicious.example.com/log"})
        responses.add(responses.GET, "https://malicious.example.com/log", status=200)

        # Custom headers that should be detected as sensitive based on their name
        sensitive_headers = {
            "X-Super-Secret-Token": "secret123",
            "My-API-Key": "key456",
            "Session-ID": "sess789",
            "Auth-Info": "creds000",
            "Cookie": "session=abc",
            "X-Custom-Password": "password1",
        }

        # Headers that should NOT be stripped (safe headers)
        safe_headers = {
            "User-Agent": "test-agent",
            "Accept": "application/json",
            "X-Correlation-ID": "uuid-1234", # ID is ambiguous? "id" is not in my list, but "session-id" is via "session".
        }

        all_headers = {**sensitive_headers, **safe_headers}

        # Send request
        session.get("https://api.example.com/", headers=all_headers)

        assert len(responses.calls) == 2

        req2 = responses.calls[1].request

        # Sensitive headers should be gone
        for header in sensitive_headers.keys():
            assert header not in req2.headers, f"Sensitive header {header} leaked to third-party host"

        # Safe headers should remain (User-Agent might be overwritten by session default, but X-Correlation-ID should stay)
        # Note: requests session adds User-Agent, so we check X-Correlation-ID
        assert "X-Correlation-ID" in req2.headers, "Safe header X-Correlation-ID was incorrectly stripped"

    run()
