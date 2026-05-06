from src.utils.http import _sanitize_url_for_error

def test_sanitize_oauth_params() -> None:
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

def test_sanitize_saml_params() -> None:
    url = "https://example.com/sso?SAMLRequest=base64_request&SAMLResponse=base64_response"
    sanitized = _sanitize_url_for_error(url)

    assert "base64_request" not in sanitized, "SAMLRequest leaked"
    assert "base64_response" not in sanitized, "SAMLResponse leaked"


def test_sanitize_bearer_assertion_param() -> None:
    """RFC 7521/7522/7523 — plain `assertion` param carries a signed credential.

    The token endpoint of a SAML/JWT bearer flow receives the entire assertion
    via the `assertion` query/body parameter. Without redaction, the signed
    payload (with user identity claims) leaks into URL-bearing error logs.
    """
    secret = "eyJraWQiOiJzaWcifQ.signedAssertionPayload.signature"
    url = (
        "https://idp.example.com/token?grant_type=urn:ietf:params:oauth:grant-type:saml2-bearer"
        f"&assertion={secret}"
    )
    sanitized = _sanitize_url_for_error(url)
    assert secret not in sanitized, "assertion parameter leaked"


def test_sanitize_device_flow_codes() -> None:
    """RFC 8628 — `device_code` is a polling secret; `user_code` pairs the user.

    Both parameters were previously not caught by URL-level redaction because
    their normalized form (`devicecode` / `usercode`) does not contain any
    sensitive substring (`token`, `secret`, etc.). They now have explicit
    entries in the sensitive query-key set.
    """
    url = (
        "https://example.com/oauth/token?grant_type=urn:ietf:params:oauth:grant-type:device_code"
        "&device_code=secret-device-code-value&user_code=ABCD-EFGH"
    )
    sanitized = _sanitize_url_for_error(url)
    assert "secret-device-code-value" not in sanitized, "device_code leaked"
    assert "ABCD-EFGH" not in sanitized, "user_code leaked"
