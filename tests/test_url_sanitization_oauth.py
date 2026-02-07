from src.utils.http import _sanitize_url_for_error

def test_sanitize_oauth_params():
    # These parameters are critical for OAuth/OIDC flows and often contain sensitive data
    # or should be protected to prevent leakage (e.g. session correlation via state).
    url = "https://example.com/callback?state=sensitive_state_value&nonce=sensitive_nonce&client_assertion=JWT_TOKEN&response_mode=form_post"
    sanitized = _sanitize_url_for_error(url)

    # State can contain correlation IDs or anti-CSRF tokens
    assert "sensitive_state_value" not in sanitized, "state parameter leaked"
    assert "state=%2A%2A%2A" in sanitized or "state=***" in sanitized

    # Nonce should be treated as sensitive in logs
    assert "sensitive_nonce" not in sanitized, "nonce parameter leaked"

    # Client assertions are effectively private keys/tokens (JWTs)
    assert "JWT_TOKEN" not in sanitized, "client_assertion leaked"

    # Response mode itself isn't secret, but often accompanies sensitive flows.
    # It is debatable if it needs redaction, but client_assertion definitely does.

def test_sanitize_saml_params():
    url = "https://example.com/sso?SAMLRequest=base64_request&SAMLResponse=base64_response"
    sanitized = _sanitize_url_for_error(url)

    assert "base64_request" not in sanitized, "SAMLRequest leaked"
    assert "base64_response" not in sanitized, "SAMLResponse leaked"
