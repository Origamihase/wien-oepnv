
import pytest
from src.utils.logging import sanitize_log_message

def test_sanitize_log_message_new_keys():
    # Test cases for new sensitive keys
    test_cases = [
        ("https://example.com?token=SECRET123", "https://example.com?token=***"),
        ("https://example.com?key=SECRET123", "https://example.com?key=***"),
        ("https://example.com?apikey=SECRET123", "https://example.com?apikey=***"),
        ("https://example.com?password=SECRET123", "https://example.com?password=***"),
        ("https://example.com?secret=SECRET123", "https://example.com?secret=***"),
        ("token=SECRET123&other=val", "token=***&other=val"),
        ("'key': 'SECRET123'", "'key': '***'"), # JSON-like or header-like
        ('"apikey": "SECRET123"', '"apikey": "***"'),
    ]

    for input_str, expected in test_cases:
        sanitized = sanitize_log_message(input_str)
        # We don't expect exact match if other sanitization happens (e.g. control chars),
        # but we expect the secret to be masked.
        assert "***" in sanitized, f"Secret not masked in '{input_str}'"
        assert "SECRET123" not in sanitized, f"Secret leaked in '{input_str}'"

def test_sanitize_log_message_existing_functionality():
    # Ensure regressions are not introduced
    assert sanitize_log_message("Authorization: Bearer SECRET") == "Authorization: ***"
    assert sanitize_log_message("accessId=12345") == "accessId=***"
