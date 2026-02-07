"""Tests for log sanitization of keys with prefixes."""

import pytest
from src.utils.logging import sanitize_log_message

@pytest.mark.parametrize(
    "input_text, expected_redacted",
    [
        # Standard Redaction (already working)
        ('{"password": "secret"}', '{"password": "***"}'),
        ('{"api_key": "secret"}', '{"api_key": "***"}'),

        # Prefix Redaction (failing before fix)
        ('{"db_password": "secret"}', '{"db_password": "***"}'),
        ('{"my_api_key": "secret"}', '{"my_api_key": "***"}'),
        ('{"github_token": "secret"}', '{"github_token": "***"}'),
        ('{"app_secret": "secret"}', '{"app_secret": "***"}'),
        ('{"aws_credential": "secret"}', '{"aws_credential": "***"}'),

        # Suffix variations
        ('{"my-api-key": "secret"}', '{"my-api-key": "***"}'),
        ('{"my.api.key": "secret"}', '{"my.api.key": "***"}'),

        # Token variations
        ('{"id_token": "secret"}', '{"id_token": "***"}'),
        ('{"refresh_token": "secret"}', '{"refresh_token": "***"}'),

        # Ensure we don't over-redact too aggressively (e.g. "key" prefix)
        # We don't want "primary_key" to be redacted if "key" is too broad,
        # unless we explicitly decide "key" means secret.
        # But "api_key" should be.
        # For "key", current implementation has "key" in _keys.
        # So {"key": "..."} is redacted.
        # But {"primary_key": "..."} is NOT redacted currently.
        # We are NOT adding `.*key` (too broad). We only add `.*api_key`.
        ('{"primary_key": "123"}', '{"primary_key": "123"}'),
    ]
)
def test_log_sanitization_prefix(input_text, expected_redacted):
    sanitized = sanitize_log_message(input_text)
    assert sanitized == expected_redacted
