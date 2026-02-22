
import responses
from unittest.mock import patch
from src.utils.http import session_with_retries

@patch("src.utils.http.verify_response_ip")
@patch("src.utils.http.validate_http_url")
def test_strip_session_headers_on_scheme_downgrade(mock_validate_url, mock_verify_ip):
    """Verify that sensitive headers set on the SESSION are stripped when redirecting to insecure scheme."""
    mock_validate_url.side_effect = lambda url, **kwargs: url
    mock_verify_ip.return_value = None

    session = session_with_retries("test-agent")
    # Set sensitive header on the SESSION
    session.headers.update({
        "Authorization": "Bearer session-token",
        "X-Api-Key": "session-api-key"
    })

    @responses.activate
    def run():
        responses.add(responses.GET, "https://secure.example.com/", status=302, headers={"Location": "http://secure.example.com/login"})
        responses.add(responses.GET, "http://secure.example.com/login", status=200)

        # Make request without explicit headers (using session headers)
        session.get("https://secure.example.com/")

        assert len(responses.calls) == 2

        # First request (HTTPS) should have session headers
        req1 = responses.calls[0].request
        assert req1.headers["Authorization"] == "Bearer session-token"
        assert req1.headers["X-Api-Key"] == "session-api-key"

        # Second request (HTTP) should NOT have sensitive headers
        req2 = responses.calls[1].request
        assert "Authorization" not in req2.headers, "Session Authorization leaked to insecure HTTP"
        assert "X-Api-Key" not in req2.headers, "Session X-Api-Key leaked to insecure HTTP"

    run()
