"""Tests for log sanitization of keys with non-standard separators."""

import pytest
from src.utils.logging import sanitize_log_message

@pytest.mark.parametrize(
    "input_text, expected_redacted",
    [
        # Double Underscores
        ('{"client__secret": "s3cr3t"}', '{"client__secret": "***"}'),
        ('client__secret=s3cr3t', 'client__secret=***'),

        # Double Hyphens
        ('{"Client--ID": "12345"}', '{"Client--ID": "***"}'),
        ('Client--ID=12345', 'Client--ID=***'),

        # Mixed Separators (rare but possible in some parsers)
        ('x_api-key=123', 'x_api-key=***'),
        ('x-api_key=123', 'x-api_key=***'),

        # Multiple Separators
        ('access___token=abc', 'access___token=***'),

        # Vendor Specific with multiple separators
        ('Ocp--Apim--Subscription--Key=key', 'Ocp--Apim--Subscription--Key=***'),
        ('Ocp__Apim__Subscription__Key=key', 'Ocp__Apim__Subscription__Key=***'),

        # Headers with variations
        ('X--Api--Key: secret', 'X--Api--Key: ***'),
    ]
)
def test_log_sanitization_separators(input_text, expected_redacted):
    sanitized = sanitize_log_message(input_text)
    assert sanitized == expected_redacted
