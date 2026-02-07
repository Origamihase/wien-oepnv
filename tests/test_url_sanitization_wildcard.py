"""Tests for wildcard/substring based URL sanitization."""

import pytest
from src.utils.http import _sanitize_url_for_error

@pytest.mark.parametrize(
    "key,value,should_redact",
    [
        # Exact matches (already covered by other tests, but good for regression)
        ("token", "secret", True),
        ("password", "secret", True),

        # Substring matches - Prefix
        ("my_token", "val1", True),
        ("user_password", "val2", True),
        ("api_secret", "val3", True),
        ("aws_credential", "val4", True),

        # Substring matches - Suffix
        ("accesstoken", "val5", True),
        ("app_secret", "val6", True),

        # Substring matches - Infix
        ("my_token_value", "val7", True),
        ("the_secret_code", "val8", True),

        # Specific keywords
        ("passphrase", "val9", True),
        ("apikey", "val10", True),
        ("accesskey", "val11", True),

        # Variations with separators
        ("api-key", "val12", True),
        ("access.key", "val13", True),
        ("my_api_key", "val14", True), # "myapikey" contains "apikey"

        # Benign keys (should NOT be redacted)
        ("sort_order", "asc", False),
        ("page_index", "1", False),
        ("public_key_id", "123", False), # "publickeyid" does not contain "apikey" or "accesskey"
        ("sort_key", "asc", False),      # "sortkey" does not contain high-risk substrings

        ("author", "name", False), # Contains "auth" but "auth" is not in substrings
        ("authorization", "bearer", True), # Exact match in _SENSITIVE_QUERY_KEYS

        # False positives (acceptable collateral)
        ("tokenizer", "gpt4", True), # Contains "token"
        ("secretary", "john", True), # Contains "secret"
    ],
)
def test_sanitize_url_wildcard(key, value, should_redact):
    url = f"https://example.com/?{key}={value}"
    sanitized = _sanitize_url_for_error(url)

    if should_redact:
        # urlencode encodes '*' as '%2A'
        assert f"{key}=%2A%2A%2A" in sanitized
        assert value not in sanitized
    else:
        assert f"{key}={value}" in sanitized

def test_sanitize_fragment_wildcard():
    url = "https://example.com/#my_token=secret123&other=safe"
    sanitized = _sanitize_url_for_error(url)
    # urlencode encodes '*' as '%2A'
    assert "my_token=%2A%2A%2A" in sanitized
    assert "secret123" not in sanitized
    assert "other=safe" in sanitized

def test_sanitize_mixed_params():
    url = "https://example.com/?safe=1&my_token=secret&also_safe=2"
    sanitized = _sanitize_url_for_error(url)
    assert "safe=1" in sanitized
    assert "also_safe=2" in sanitized
    # urlencode encodes '*' as '%2A'
    assert "my_token=%2A%2A%2A" in sanitized
    assert "secret" not in sanitized
