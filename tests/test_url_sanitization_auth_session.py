import pytest
from src.utils.http import _sanitize_url_for_error

def test_sanitize_url_auth_variations():
    """Test that 'auth' related substrings trigger redaction."""
    # 'auth' itself is not in substrings due to false positives (author),
    # but 'authorization' and 'token' are.
    urls = [
        # "https://example.com?my_auth=VALUE123", # Known limitation: 'auth' is too broad
        "https://example.com?auth_token=VALUE123", # Covered by 'token'
        # "https://example.com?user_auth_code=VALUE123", # 'code'/ 'auth' not covered
        "https://example.com?authorization_header=VALUE123", # Covered by 'authorization'
        "https://example.com?my_authorization=VALUE123", # Covered by 'authorization'
    ]
    for url in urls:
        sanitized = _sanitize_url_for_error(url)
        assert "VALUE123" not in sanitized, f"Failed to redact {url}"
        assert "***" in sanitized or "%2A%2A%2A" in sanitized

def test_sanitize_url_session_variations():
    """Test that 'session' substring triggers redaction."""
    urls = [
        "https://example.com?user_session=VALUE123",
        "https://example.com?session_id=VALUE123",
        "https://example.com?app_session=VALUE123",
    ]
    for url in urls:
        sanitized = _sanitize_url_for_error(url)
        assert "VALUE123" not in sanitized, f"Failed to redact {url}"

def test_sanitize_url_cookie_variations():
    """Test that 'cookie' substring triggers redaction."""
    urls = [
        "https://example.com?my_cookie=VALUE123",
        "https://example.com?cookie_consent=VALUE123",
        "https://example.com?auth_cookie=VALUE123",
    ]
    for url in urls:
        sanitized = _sanitize_url_for_error(url)
        assert "VALUE123" not in sanitized, f"Failed to redact {url}"

def test_sanitize_url_client_variations():
    """Test that 'clientid' and 'clientsecret' substrings trigger redaction."""
    urls = [
        "https://example.com?my_client_id=VALUE123",
        "https://example.com?app_client_id=VALUE123",
        "https://example.com?my_client_secret=VALUE123",
    ]
    for url in urls:
        sanitized = _sanitize_url_for_error(url)
        assert "VALUE123" not in sanitized, f"Failed to redact {url}"

def test_sanitize_url_authorization_variations():
    """Test that 'authorization' substring triggers redaction."""
    urls = [
        "https://example.com?my_authorization=VALUE123",
        "https://example.com?authorization_code=VALUE123",
    ]
    for url in urls:
        sanitized = _sanitize_url_for_error(url)
        assert "VALUE123" not in sanitized, f"Failed to redact {url}"
