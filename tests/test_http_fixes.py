import pytest
import re
import requests
from unittest.mock import MagicMock, patch
from src.utils.http import verify_response_ip, request_safe, _is_sensitive_header, _sanitize_url_for_error, _SENSITIVE_HEADERS

# 1. Socket Access Tests
def test_verify_response_ip_new_urllib3():
    """Test verify_response_ip with newer urllib3 using _connection."""
    response = MagicMock(spec=requests.Response)
    response.url = "http://example.com"

    # Mock raw object
    raw = MagicMock()
    del raw.connection # Ensure 'connection' attribute does not exist

    # Create a mock connection with a sock
    connection = MagicMock()
    sock = MagicMock()
    sock.getpeername.return_value = ("1.1.1.1", 80) # Safe IP
    connection.sock = sock

    # Attach to _connection
    raw._connection = connection
    response.raw = raw

    # This should pass without raising ValueError or AttributeError
    verify_response_ip(response)

def test_verify_response_ip_old_urllib3():
    """Test verify_response_ip with older urllib3 using connection."""
    response = MagicMock(spec=requests.Response)
    response.url = "http://example.com"

    # Mock raw object
    raw = MagicMock()
    del raw._connection # Ensure '_connection' attribute does not exist for this test

    connection = MagicMock()
    sock = MagicMock()
    sock.getpeername.return_value = ("1.1.1.1", 80) # Safe IP
    connection.sock = sock

    raw.connection = connection
    response.raw = raw

    verify_response_ip(response)

# 2. Case-sensitivity Tests
def test_request_safe_headers_case_sensitivity():
    """Test that request_safe handles case-insensitive headers correctly."""
    session = requests.Session()

    # We want to verify that Content-Type is popped even if passed as content-type.
    # We mock verify_response_ip to avoid dealing with socket mocking complexity here.
    with patch("src.utils.http.verify_response_ip") as mock_verify_ip:
        with patch("src.utils.http.requests.Session.request") as mock_request:
            # Mock the first response to be a 303 redirect
            resp1 = MagicMock()
            resp1.status_code = 303
            resp1.headers = {"Location": "http://example.com/new"}
            resp1.url = "http://example.com/old"
            resp1.is_redirect = True

            # Mock the second response (success)
            resp2 = MagicMock()
            resp2.status_code = 200
            resp2._content = b"ok"
            resp2.is_redirect = False

            # We need session.request to return a context manager
            cm1 = MagicMock()
            cm1.__enter__.return_value = resp1

            cm2 = MagicMock()
            cm2.__enter__.return_value = resp2

            mock_request.side_effect = [cm1, cm2]

            # Pass headers with weird casing
            headers = {"conTENT-TyPE": "application/json", "cONtenT-leNGth": "123"}

            # Call request_safe with POST
            request_safe(session, "http://example.com/old", method="POST", headers=headers)

            # Verify the second call to session.request (the redirect)
            # It should be GET, and headers should NOT contain Content-Type/Length
            assert mock_request.call_count == 2
            call_args = mock_request.call_args_list[1]
            _, kwargs = call_args

            sent_headers = kwargs.get("headers", {})
            # If request_safe uses CaseInsensitiveDict, 'content-type' in sent_headers will work
            # BUT if it converts to dict internally or passes it as is...
            # request_safe passes kwargs to session.request.
            # If we fixed it, kwargs['headers'] will be CaseInsensitiveDict (or behave like one).
            # If not fixed, it's a standard dict, and 'Content-Type' was popped?
            # Wait, the code pops "Content-Type" and "Content-Length" (title case).
            # If input was "conTENT-TyPE", standard dict pop("Content-Type") won't remove it.
            # So "conTENT-TyPE" would remain.

            # We check if ANY case variation of content-type exists.
            sent_keys = {k.lower() for k in sent_headers.keys()}
            assert "content-type" not in sent_keys
            assert "content-length" not in sent_keys

def test_is_sensitive_header_case_sensitivity_explicit():
    """Test that _is_sensitive_header is case-insensitive for explicit headers."""
    # We pick a header from _SENSITIVE_HEADERS and test mixed case.
    # "X-CSRF-Token" is in _SENSITIVE_HEADERS.
    # It also contains "token", so it matches partials too.
    # But let's assume we want to ensure the explicit check works case-insensitively too.
    # Or find one that doesn't match partials easily?
    # "X-Goog-Api-Key" -> key.
    # "Cookie" -> cookie.
    # It seems almost all match partials.
    # But regardless, we want to ensure consistent behavior.
    assert _is_sensitive_header("X-CSRF-TOKEN")
    assert _is_sensitive_header("x-csrf-token")
    assert _is_sensitive_header("X-Csrf-Token")

# 3. Regex Robustness Tests
def test_sanitize_url_missing_slash_group():
    """Test _sanitize_url_for_error with URL missing slashes (https:user:pass@...)."""
    url = "https:user:password@example.com/foo"
    sanitized = _sanitize_url_for_error(url)

    # It should be sanitized.
    # Current behavior (and desired behavior) is that it strips sensitive info.
    # The regex replacement ensures it becomes https:***@... which then might be parsed or returned.
    # If urlparse handles it as opaque, it might keep ***@.
    # If urlparse handles it as hierarchical (after fixing slashes?), it strips it.
    # We just want to ensure password is GONE.
    assert "password" not in sanitized
    assert "user" not in sanitized # Should be stripped too

def test_sanitize_url_standard():
    url = "https://user:password@example.com/foo"
    sanitized = _sanitize_url_for_error(url)
    assert "password" not in sanitized
    # Standard behavior strips auth completely
    assert sanitized == "https://example.com/foo"
