
import pytest
from src.utils.http import _sanitize_url_for_error

def test_sanitize_url_redacts_fragment_secrets():
    """Verify that sensitive keys in the URL fragment are redacted."""
    # OIDC implicit flow example
    url = "https://example.com/callback#access_token=SUPER_SECRET_TOKEN&state=xyz&token_type=Bearer"
    sanitized = _sanitize_url_for_error(url)

    assert "SUPER_SECRET_TOKEN" not in sanitized
    # urlencode encodes '*' as '%2A'
    assert "access_token=%2A%2A%2A" in sanitized
    assert "state=xyz" in sanitized
    # token_type contains "token", so it's redacted by the substring rule.
    # This is acceptable collateral for safer error logging.
    assert "token_type=%2A%2A%2A" in sanitized

def test_sanitize_url_leaves_benign_fragments():
    """Verify that benign fragments (anchors) are preserved."""
    url = "https://example.com/docs#section-1"
    sanitized = _sanitize_url_for_error(url)
    assert sanitized == url

def test_sanitize_url_fragment_mixed_case():
    """Verify that case variations in fragment keys are handled."""
    url = "https://example.com/#Token=secret"
    sanitized = _sanitize_url_for_error(url)
    assert "secret" not in sanitized
    assert "Token=%2A%2A%2A" in sanitized
