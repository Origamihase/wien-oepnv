import pytest
from src.utils.http import _sanitize_url_for_error

def test_sanitize_url_basic_auth():
    url = "https://user:pass@example.com/foo"
    sanitized = _sanitize_url_for_error(url)
    assert "user" not in sanitized
    assert "pass" not in sanitized
    assert "example.com" in sanitized
    assert sanitized == "https://example.com/foo"

def test_sanitize_url_query_params():
    url = "https://example.com/api?accessId=SECRET&foo=bar"
    sanitized = _sanitize_url_for_error(url)
    assert "SECRET" not in sanitized
    assert "accessId=***" in sanitized or "accessId=REDACTED" in sanitized or "accessId" in sanitized # Depending on implementation
    assert "foo=bar" in sanitized

def test_sanitize_url_multiple_sensitive():
    url = "https://example.com/api?token=123&key=abc&public=yes"
    sanitized = _sanitize_url_for_error(url)
    assert "123" not in sanitized
    assert "abc" not in sanitized
    assert "yes" in sanitized

def test_sanitize_url_mixed_auth_and_query():
    url = "https://user:pass@example.com/api?accessId=SECRET"
    sanitized = _sanitize_url_for_error(url)
    assert "user" not in sanitized
    assert "pass" not in sanitized
    assert "SECRET" not in sanitized
    assert "example.com" in sanitized

def test_sanitize_url_malformed():
    url = "http://["
    sanitized = _sanitize_url_for_error(url)
    assert sanitized == "invalid_url"

def test_sanitize_url_enhanced_keys():
    # Verify that new keys are redacted
    # jsessionid is case-insensitive in check
    url = "https://example.com/api?jsessionid=SECRET&auth_token=TOKEN123&api_key=KEY456"
    sanitized = _sanitize_url_for_error(url)
    assert "SECRET" not in sanitized
    assert "TOKEN123" not in sanitized
    assert "KEY456" not in sanitized
    # urlencode encodes '*' as '%2A'
    assert "jsessionid=%2A%2A%2A" in sanitized or "jsessionid=***" in sanitized
    assert "auth_token=%2A%2A%2A" in sanitized or "auth_token=***" in sanitized
    assert "api_key=%2A%2A%2A" in sanitized or "api_key=***" in sanitized

    # Check asp.net_sessionid
    url2 = "https://example.com/api?asp.net_sessionid=SESS&bearer_token=BEARER"
    sanitized2 = _sanitize_url_for_error(url2)
    assert "SESS" not in sanitized2
    assert "BEARER" not in sanitized2
    assert "asp.net_sessionid=%2A%2A%2A" in sanitized2 or "asp.net_sessionid=***" in sanitized2
    assert "bearer_token=%2A%2A%2A" in sanitized2 or "bearer_token=***" in sanitized2

def test_sanitize_url_subscription_keys():
    # Verify new keys added in fix
    url = "https://example.com/api?Ocp-Apim-Subscription-Key=SECRET1&x-api-key=SECRET2&subscription-key=SECRET3"
    sanitized = _sanitize_url_for_error(url)
    assert "SECRET1" not in sanitized
    assert "SECRET2" not in sanitized
    assert "SECRET3" not in sanitized
    assert "Ocp-Apim-Subscription-Key=%2A%2A%2A" in sanitized
    assert "x-api-key=%2A%2A%2A" in sanitized
    assert "subscription-key=%2A%2A%2A" in sanitized
