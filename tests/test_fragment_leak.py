
from src.utils.http import _sanitize_url_for_error

def test_sanitize_url_redacts_fragment_secrets():
    """Verify that URL fragments are entirely stripped."""
    # OIDC implicit flow example
    url = "https://example.com/callback#access_token=SUPER_SECRET_TOKEN&state=xyz&token_type=Bearer"
    sanitized = _sanitize_url_for_error(url)

    assert "SUPER_SECRET_TOKEN" not in sanitized
    assert "access_token" not in sanitized
    assert "#" not in sanitized

def test_sanitize_url_leaves_benign_fragments():
    """Verify that URL fragments are stripped entirely (benign or not)."""
    url = "https://example.com/docs#section-1"
    sanitized = _sanitize_url_for_error(url)
    assert sanitized == "https://example.com/docs"

def test_sanitize_url_fragment_mixed_case():
    """Verify that URL fragments are stripped entirely regardless of case."""
    url = "https://example.com/#Token=secret"
    sanitized = _sanitize_url_for_error(url)
    assert "secret" not in sanitized
    assert "Token" not in sanitized
