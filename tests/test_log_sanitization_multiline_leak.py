import pytest
from src.utils.logging import sanitize_log_message

def test_space_leakage_unquoted():
    """Test handling of unquoted secrets with spaces."""
    # "password=my secret user=1"
    # New behavior stops at space to avoid over-redaction of subsequent text.
    # This means "secret" (the second part of the password) will leak if unquoted.
    # Users should quote secrets with spaces: password="my secret"

    msg = "password=my secret user=1"
    sanitized = sanitize_log_message(msg)

    # We expect "my" to be redacted as it's the first token.
    # "secret" leaks because we stop at space.
    assert "secret" in sanitized
    # "user=1" is preserved.
    assert "user=1" in sanitized

    assert sanitized == "password=*** secret user=1"

def test_pem_block_redaction():
    """Test that PEM blocks (keys/certs) are fully redacted."""
    pem = """-----BEGIN PRIVATE KEY-----
MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQD
...
-----END PRIVATE KEY-----"""

    msg = f"Loaded key:\n{pem}\nNext line."
    sanitized = sanitize_log_message(msg)

    assert "MIIEvg" not in sanitized, "PEM content leaked!"
    assert "-----BEGIN PRIVATE KEY-----" in sanitized
    assert "-----END PRIVATE KEY-----" in sanitized
    assert "***" in sanitized

def test_multiline_header_leak():
    """Test that multiline headers (not indented) don't leak subsequent lines."""
    # Using 'Private-Key' which matches 'key' or 'private' in _header_keys
    pem = """-----BEGIN PRIVATE KEY-----
MIIEvg...
-----END PRIVATE KEY-----"""
    msg = f"Private-Key: {pem}"
    sanitized = sanitize_log_message(msg)

    assert "MIIEvg" not in sanitized, "PEM content in header leaked!"

def test_ampersand_separation():
    """Test that & acts as separator."""
    msg = "password=val&user=1"
    sanitized = sanitize_log_message(msg)
    assert sanitized == "password=***&user=1"

def test_comma_separation():
    """Test that comma acts as separator (common in repr)."""
    msg = "password=val,user=1"
    sanitized = sanitize_log_message(msg)
    assert sanitized == "password=***,user=1"

def test_space_separated_keys():
    """Test multiple space separated keys."""
    # usage of api_key (matches [a-z0-9_.\-]*api[-_.\s]*key)
    # usage of client_secret (matches client[-_.\s]*secret)
    msg = "api_key=secret1 client_secret=secret2 user_id=123"
    sanitized = sanitize_log_message(msg)

    assert "secret1" not in sanitized
    assert "secret2" not in sanitized
    assert "user_id=123" in sanitized # user_id is not sensitive, should be preserved.

def test_over_redaction_space_followed_by_text():
    """Test that text following a key-value pair is NOT redacted if it doesn't look like a key."""
    msg = "password=secret123 and some other text"
    sanitized = sanitize_log_message(msg)
    # With the fix, we expect: "password=*** and some other text"
    expected = "password=*** and some other text"
    assert sanitized == expected, f"Over-redaction occurred: expected '{expected}', got '{sanitized}'"

def test_over_redaction_space_followed_by_key_lookalike():
    """Test that text that looks like a key but is not (no '=') is not consumed."""
    msg = "api_key=secret foo bar"
    sanitized = sanitize_log_message(msg)
    expected = "api_key=*** foo bar"
    assert sanitized == expected
