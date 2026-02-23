
from unittest.mock import MagicMock, patch
from src.utils.http import _strip_sensitive_headers, request_safe, DEFAULT_TIMEOUT

def test_strip_sensitive_headers_port_change():
    headers = {"Authorization": "secret", "X-Custom": "safe"}
    # Same host, different port (8443 -> 9443)
    _strip_sensitive_headers(headers, "https://example.com:8443", "https://example.com:9443")
    assert "Authorization" not in headers
    assert "X-Custom" in headers

def test_strip_sensitive_headers_same_port_default():
    headers = {"Authorization": "secret"}
    # Same host, default port implicit (https -> 443)
    _strip_sensitive_headers(headers, "https://example.com", "https://example.com:443")
    assert "Authorization" in headers

def test_request_safe_enforces_timeout():
    session = MagicMock()
    # When request_safe is called with timeout=None
    # It should pass DEFAULT_TIMEOUT to session.request

    # Mock response
    mock_response = MagicMock()
    mock_response.is_redirect = False
    mock_response.headers = {}
    mock_response.iter_content.return_value = [b"data"]

    # Mock context manager
    session.request.return_value.__enter__.return_value = mock_response

    # We need to mock validate_http_url and _resolve_hostname_safe to avoid network calls
    with patch("src.utils.http.validate_http_url", return_value="https://example.com"), \
         patch("src.utils.http._resolve_hostname_safe", return_value=[(None, None, None, None, ("1.1.1.1", 443))]), \
         patch("src.utils.http.is_ip_safe", return_value=True):

        request_safe(session, "https://example.com", timeout=None)

        # Verify call args
        args, kwargs = session.request.call_args
        assert kwargs["timeout"] == DEFAULT_TIMEOUT

def test_request_safe_respects_explicit_timeout():
    session = MagicMock()
    mock_response = MagicMock()
    mock_response.is_redirect = False
    mock_response.headers = {}
    mock_response.iter_content.return_value = [b"data"]
    session.request.return_value.__enter__.return_value = mock_response

    with patch("src.utils.http.validate_http_url", return_value="https://example.com"), \
         patch("src.utils.http._resolve_hostname_safe", return_value=[(None, None, None, None, ("1.1.1.1", 443))]), \
         patch("src.utils.http.is_ip_safe", return_value=True):

        request_safe(session, "https://example.com", timeout=10)

        args, kwargs = session.request.call_args
        # Note: request_safe calculates remaining time, but for the first call
        # it should be close to 10 (or exactly 10 if we mock time.monotonic)
        # But request_safe passes `remaining_time` which is calculated.
        # Since we didn't mock time, it might be 9.999...
        assert 9.0 < kwargs["timeout"] <= 10.0
