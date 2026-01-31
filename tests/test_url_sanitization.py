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
