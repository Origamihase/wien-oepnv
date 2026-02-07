
import pytest
from src.utils.http import _sanitize_url_for_error
from src.utils.logging import sanitize_log_message
from src.utils.env import sanitize_log_message as sanitize_env_log_message

class TestRedactionEdgeCases:
    """Test suite for edge cases in secret redaction (dots, spaces, mixed casing)."""

    def test_sanitize_url_dots_and_spaces(self):
        """Test URL parameter sanitization with dots and spaces in keys."""
        cases = [
            ("https://example.com/api?api.key=secret", "https://example.com/api?api.key=%2A%2A%2A"),
            ("https://example.com/api?api%20key=secret", "https://example.com/api?api+key=%2A%2A%2A"),
            ("https://example.com/api?Client.ID=secret", "https://example.com/api?Client.ID=%2A%2A%2A"),
            ("https://example.com/api?api-key=secret", "https://example.com/api?api-key=%2A%2A%2A"),
            ("https://example.com/api?API_KEY=secret", "https://example.com/api?API_KEY=%2A%2A%2A"),
        ]
        for url, expected in cases:
            assert _sanitize_url_for_error(url) == expected

    def test_sanitize_log_message_dots_and_spaces(self):
        """Test log message sanitization with dots and spaces in keys."""
        cases = [
            # Query params in string
            ("Request to https://example.com/api?api.key=secret", "Request to https://example.com/api?api.key=***"),
            ("Request to https://example.com/api?api%20key=secret", "Request to https://example.com/api?api%20key=***"),

            # JSON-like structures
            ('{"api.key": "secret"}', '{"api.key": "***"}'),
            ('{"Client.ID": "secret"}', '{"Client.ID": "***"}'),
            ('{"api key": "secret"}', '{"api key": "***"}'),

            # Headers style
            ("Api Key: secret", "Api Key: ***"),
            ("Client.ID: secret", "Client.ID: ***"),
            ("X-Api-Key: secret", "X-Api-Key: ***"),
        ]

        for msg, expected in cases:
            assert sanitize_log_message(msg) == expected
            # Also test the fallback implementation in env.py
            assert sanitize_env_log_message(msg) == expected

    def test_sanitize_log_message_complex_separators(self):
        """Test keys with mixed separators."""
        cases = [
            ('{"access.token": "secret"}', '{"access.token": "***"}'),
            ('{"api.key": "val"}', '{"api.key": "***"}'),
            ('{"Client.ID": "123"}', '{"Client.ID": "***"}'),
        ]

        for msg, expected in cases:
            assert sanitize_log_message(msg) == expected
            assert sanitize_env_log_message(msg) == expected
