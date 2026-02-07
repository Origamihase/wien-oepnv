
import pytest
import re
from src.utils.logging import sanitize_log_message

def test_sanitize_log_message_truncation():
    """Verify that sanitize_log_message does not truncate subsequent lines."""
    # A log message with a sensitive header followed by other headers
    log_msg = (
        "Request Headers:\n"
        "X-Api-Key: super_secret_key\n"
        "User-Agent: my-app/1.0\n"
        "Accept: application/json"
    )

    sanitized = sanitize_log_message(log_msg)

    # Check that the secret is redacted
    assert "super_secret_key" not in sanitized
    assert "X-Api-Key: ***" in sanitized

    # Check that subsequent headers are PRESERVED
    assert "User-Agent: my-app/1.0" in sanitized, "User-Agent header was truncated!"
    assert "Accept: application/json" in sanitized, "Accept header was truncated!"

def test_sanitize_log_message_multiline_secret_indented():
    """Verify that indented multiline secrets are still redacted."""
    # A log message with a multiline secret (indented)
    log_msg = (
        "Authorization: Bearer\n"
        "  eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6Ik\n"
        "  John DoeIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )

    sanitized = sanitize_log_message(log_msg)

    assert "eyJhbGci" not in sanitized
    assert "Authorization: ***" in sanitized

def test_cookie_truncation():
    """Verify that Cookie header does not truncate subsequent lines."""
    log_msg = "Cookie: session=123\nReferer: https://example.com"
    sanitized = sanitize_log_message(log_msg)
    assert "session=123" not in sanitized
    assert "Cookie: ***" in sanitized
    assert "Referer: https://example.com" in sanitized

def test_authorization_truncation():
    """Verify that Authorization header does not truncate subsequent lines."""
    log_msg = "Authorization: Bearer 123\nContent-Type: application/json"
    sanitized = sanitize_log_message(log_msg)
    assert "Bearer 123" not in sanitized
    assert "Authorization: ***" in sanitized
    assert "Content-Type: application/json" in sanitized
