
import pytest
from src.utils.http import _sanitize_url_for_error

@pytest.mark.parametrize("key", ["jwt", "dsn", "otp", "my_webhook_url"])
def test_sensitive_keys_redaction(key):
    """Verify that these sensitive keys are now correctly redacted."""
    secret = "secret123"
    url = f"https://example.com/api?{key}={secret}"
    sanitized = _sanitize_url_for_error(url)
    # AFTER THE FIX: We expect the secret to be REDACTED
    assert secret not in sanitized, f"Key {key} was NOT redacted! (sanitized: {sanitized})"
    # The value '***' is URL-encoded as '%2A%2A%2A'
    assert "%2A%2A%2A" in sanitized or "***" in sanitized

def test_harmless_param():
    """Verify harmless parameters remain untouched."""
    url = "https://example.com/api?page=1&sort=desc"
    sanitized = _sanitize_url_for_error(url)
    assert "page=1" in sanitized
    assert "sort=desc" in sanitized
