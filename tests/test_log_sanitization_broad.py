"""Tests for broadened log sanitization of 'secret' and 'password'."""

import pytest
from src.utils.logging import sanitize_log_message

@pytest.mark.parametrize(
    "input_text, expected_redacted",
    [
        # Broad 'secret' matching
        ('{"secret_key": "123"}', '{"secret_key": "***"}'),
        ('{"app_secret_token": "123"}', '{"app_secret_token": "***"}'),
        ('{"my_secret": "123"}', '{"my_secret": "***"}'),
        ('{"top_secret_info": "123"}', '{"top_secret_info": "***"}'),

        # Broad 'password' matching
        ('{"password_hash": "abc"}', '{"password_hash": "***"}'),
        ('{"user_password_encrypted": "abc"}', '{"user_password_encrypted": "***"}'),
        ('{"password": "abc"}', '{"password": "***"}'),

        # Broad 'passphrase' matching
        ('{"passphrase_hint": "abc"}', '{"passphrase_hint": "***"}'),

        # Ensure we don't regress on existing behavior
        ('{"client_secret": "abc"}', '{"client_secret": "***"}'),
        ('{"api_key": "abc"}', '{"api_key": "***"}'),
    ]
)
def test_log_sanitization_broad(input_text, expected_redacted):
    sanitized = sanitize_log_message(input_text)
    assert sanitized == expected_redacted
