import pytest
import responses
from unittest.mock import patch
from src.utils.http import session_with_retries

@patch("src.utils.http.verify_response_ip")
@patch("src.utils.http.validate_http_url")
def test_strip_extended_sensitive_headers(mock_validate_url, mock_verify_ip):
    """Verify that an extended list of sensitive headers are stripped on cross-origin redirects."""
    # Allow any URL for this test
    mock_validate_url.side_effect = lambda url, **kwargs: url
    mock_verify_ip.return_value = None

    session = session_with_retries("test-agent")

    @responses.activate
    def run():
        # Setup redirect: host1 -> host2
        responses.add(responses.GET, "https://api.example.com/", status=302, headers={"Location": "https://malicious.example.com/log"})
        responses.add(responses.GET, "https://malicious.example.com/log", status=200)

        sensitive_headers = {
            "X-Shopify-Access-Token": "shpat_1234567890abcdef",
            "X-Slack-Token": "xoxb-1234-5678-abcdef",
            "X-GitHub-Token": "ghp_abcdef1234567890",
            "X-HubSpot-API-Key": "hapikey-12345678-abcd-1234",
            "X-Postmark-Server-Token": "abcdef-1234-5678-90ab",
            "X-Postmark-Account-Token": "abcdef-1234-5678-90ab",
            "X-RapidAPI-Key": "rapidapi-key-value",
            "X-Service-Token": "service-token-value",
            "X-Access-Token": "access-token-value",
            "X-CSRF-Token": "csrf-token-value",
            "X-CSRFToken": "csrftoken-value",
            "X-XSRF-TOKEN": "xsrf-token-value",
        }

        # Send request with all these headers
        session.get("https://api.example.com/", headers=sensitive_headers)

        assert len(responses.calls) == 2

        # First request should have them
        req1 = responses.calls[0].request
        for header, value in sensitive_headers.items():
            assert req1.headers[header] == value, f"Header {header} missing in initial request"

        # Second request should NOT have them
        req2 = responses.calls[1].request
        for header in sensitive_headers.keys():
            assert header not in req2.headers, f"Header {header} leaked to third-party host"

    run()
