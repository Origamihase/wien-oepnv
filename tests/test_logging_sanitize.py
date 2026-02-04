
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

def test_sanitize_log_message_enhanced_keys():
    # Verify that new keys are redacted in query parameters
    test_cases = [
        ("https://example.com?bearer_token=SECRET123", "https://example.com?bearer_token=***"),
        ("https://example.com?api_key=SECRET123", "https://example.com?api_key=***"),
        ("https://example.com?auth_token=SECRET123", "https://example.com?auth_token=***"),
        ("https://example.com?authorization=SECRET123", "https://example.com?authorization=***"),
        ("https://example.com?auth=SECRET123", "https://example.com?auth=***"),
    ]

    for input_str, expected in test_cases:
        sanitized = sanitize_log_message(input_str)
        assert "***" in sanitized, f"Secret not masked in '{input_str}'"
        assert "SECRET123" not in sanitized, f"Secret leaked in '{input_str}'"
        # Check that the key itself is preserved (the part before =)
        key = input_str.split("=")[0].split("?")[1]
        assert key in sanitized


def test_sanitize_log_message_extended_identity_keys():
    # Verify that extended identity keys (tenant, oid, etc) are redacted
    test_cases = [
        ("https://example.com?tenant=SECRET123", "tenant"),
        ("https://example.com?tenant_id=SECRET123", "tenant_id"),
        ("https://example.com?subscription=SECRET123", "subscription"),
        ("https://example.com?subscription_id=SECRET123", "subscription_id"),
        ("https://example.com?oid=SECRET123", "oid"),
        ("https://example.com?object_id=SECRET123", "object_id"),
        ("https://example.com?code_challenge=SECRET123", "code_challenge"),
        ("https://example.com?code_verifier=SECRET123", "code_verifier"),
    ]

    for input_str, key in test_cases:
        sanitized = sanitize_log_message(input_str)
        assert "***" in sanitized, f"Secret not masked in '{input_str}'"
        assert "SECRET123" not in sanitized, f"Secret leaked in '{input_str}'"
        assert key in sanitized, f"Key '{key}' should remain visible"

def test_sanitize_log_message_quoted_spaces():
    """Verify that secrets with spaces in quotes are fully masked."""
    test_cases = [
        # Double quotes with space
        ('token="secret value"', 'token=***'),
        # Single quotes with space
        ("token='secret value'", "token=***"),
        # Quoted empty string
        ('token=""', 'token=***'),
        # JSON-like with spaces (although JSON regex usually handles this, this tests the fallback/generic param regex)
        ('key="val 1"&other=val2', 'key=***&other=val2'),
        # Mixed quotes - ensure multiple replacements work
        ("key='val 1' & secret=\"val 2\"", "key=*** & secret=***"),
    ]

    for input_str, expected_contain in test_cases:
        sanitized = sanitize_log_message(input_str)
        # We assert that the output contains the masked version
        # AND does not contain the leaked suffix
        assert expected_contain in sanitized, f"Failed to mask: {input_str} -> {sanitized}"

        # Explicit check for leakage
        if "secret value" in input_str:
            assert "value" not in sanitized, f"Leaked part of secret in '{input_str}' -> '{sanitized}'"
            assert "secret" not in sanitized
