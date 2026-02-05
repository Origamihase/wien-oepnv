import pytest
from src.utils.http import _sanitize_url_for_error

def test_sanitize_url_robust_variations():
    """Test that URL sanitization handles key variations (case, hyphens, underscores)."""

    # Variations of 'client_id'
    assert "SECRET" not in _sanitize_url_for_error("https://example.com?client-id=SECRET")
    assert "SECRET" not in _sanitize_url_for_error("https://example.com?Client_ID=SECRET")
    assert "SECRET" not in _sanitize_url_for_error("https://example.com?clientId=SECRET")

    # Variations of 'x-api-key'
    assert "SECRET" not in _sanitize_url_for_error("https://example.com?x_api_key=SECRET")
    assert "SECRET" not in _sanitize_url_for_error("https://example.com?X-API-KEY=SECRET")
    assert "SECRET" not in _sanitize_url_for_error("https://example.com?xapikey=SECRET")

    # Variations of 'tenant_id'
    assert "SECRET" not in _sanitize_url_for_error("https://example.com?TenantID=SECRET")
    assert "SECRET" not in _sanitize_url_for_error("https://example.com?tenant-id=SECRET")

    # Variations of 'subscription-key'
    assert "SECRET" not in _sanitize_url_for_error("https://example.com?SubscriptionKey=SECRET")
    assert "SECRET" not in _sanitize_url_for_error("https://example.com?subscription_key=SECRET")

def test_sanitize_url_mixed_keys():
    """Test mixed valid and sensitive keys with variations."""
    url = "https://example.com?public=ok&Client-ID=SECRET1&x_api_key=SECRET2"
    sanitized = _sanitize_url_for_error(url)

    assert "ok" in sanitized
    assert "SECRET1" not in sanitized
    assert "SECRET2" not in sanitized
    assert "Client-ID=***" in sanitized or "Client-ID=%2A%2A%2A" in sanitized
    assert "x_api_key=***" in sanitized or "x_api_key=%2A%2A%2A" in sanitized

def test_sanitize_url_existing_keys_still_work():
    """Ensure standard keys are still caught."""
    url = "https://example.com?access_token=SECRET&password=SECRET"
    sanitized = _sanitize_url_for_error(url)
    assert "SECRET" not in sanitized
