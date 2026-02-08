
import pytest
from src.utils.logging import sanitize_log_message

def test_sanitization_multiword_unquoted_secret():
    """
    Test that multi-word unquoted secrets (e.g. passphrase) are fully redacted.
    This was previously leaking words after the first space.
    """
    msg = "passphrase=correct horse battery staple"
    sanitized = sanitize_log_message(msg)

    # Expect full redaction of the value
    assert "***" in sanitized
    assert "horse" not in sanitized
    assert "battery" not in sanitized
    assert "staple" not in sanitized
    assert sanitized == "passphrase=***"

def test_sanitization_multiword_followed_by_key():
    """
    Test that unquoted value followed by a new key assignment stops correctly.
    """
    msg = "password=secret123 user=me"
    sanitized = sanitize_log_message(msg)

    # 'secret123' should be redacted
    assert "secret123" not in sanitized
    # 'user=me' should be preserved (as 'user' is not a sensitive key in this context)
    assert "user=me" in sanitized

    # If user was a sensitive key (e.g. token), it should be redacted separately
    msg2 = "password=secret123 token=foo"
    sanitized2 = sanitize_log_message(msg2)
    assert "secret123" not in sanitized2
    assert "foo" not in sanitized2
    assert sanitized2 == "password=*** token=***"

def test_sanitization_multiword_followed_by_non_key_text():
    """
    Test behavior when unquoted value is followed by text that is NOT a key assignment.
    With the new stricter regex, this text is considered part of the value and redacted.
    This is acceptable over-redaction to prevent partial leaks.
    """
    msg = "token=123 (expired)"
    sanitized = sanitize_log_message(msg)

    # '123' redacted
    assert "123" not in sanitized
    # '(expired)' is also redacted because it's not a key assignment
    assert "(expired)" not in sanitized
    assert sanitized == "token=***"

def test_sanitization_separators():
    """Test that standard separators still work correctly."""
    msg = "password=secret, user=me"
    sanitized = sanitize_log_message(msg)
    assert "secret" not in sanitized
    assert "user=me" in sanitized
    assert sanitized == "password=***, user=me"

    msg2 = "password=secret&user=me"
    sanitized2 = sanitize_log_message(msg2)
    assert "secret" not in sanitized2
    assert "user=me" in sanitized2
    assert sanitized2 == "password=***&user=me"

def test_sanitization_newline():
    """Test that newline stops unquoted value matching."""
    msg = "password=secret\nuser=me"
    sanitized = sanitize_log_message(msg)
    assert "secret" not in sanitized
    # Newlines are escaped by default in output
    assert "user=me" in sanitized
