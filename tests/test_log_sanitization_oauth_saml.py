import pytest
from src.utils.logging import sanitize_log_message

@pytest.mark.parametrize("key", [
    "client_assertion",
    "client_assertion_type",
    "saml_request",
    "SAMLRequest",
    "saml_response",
    "SAMLResponse",
    "nonce",
    "state",
])
def test_sensitive_oauth_saml_keys_redaction(key: str) -> None:
    secret = "supersecretvalue"
    # Test query param style
    msg = f"Request failed with {key}={secret}"
    sanitized = sanitize_log_message(msg)
    assert secret not in sanitized
    assert "***" in sanitized

    # Test JSON style
    msg_json = f'{{"{key}": "{secret}"}}'
    sanitized_json = sanitize_log_message(msg_json)
    assert secret not in sanitized_json
    assert "***" in sanitized_json

    # Test Header style (for relevant keys that might appear in headers)
    msg_header = f"{key}: {secret}"
    sanitized_header = sanitize_log_message(msg_header)
    assert secret not in sanitized_header
    assert "***" in sanitized_header

def test_nonce_state_redaction() -> None:
    # Specific test for short keys to ensure no false positives or negatives
    # Note: current implementation is aggressive (substring matching), so we expect redaction.

    # Nonce
    msg = "nonce=123456"
    assert "***" in sanitize_log_message(msg)
    assert "123456" not in sanitize_log_message(msg)

    # State
    msg = "state=xyz-123"
    assert "***" in sanitize_log_message(msg)
    assert "xyz-123" not in sanitize_log_message(msg)


@pytest.mark.parametrize("key", [
    # Plain assertion (RFC 7521/7522/7523 — SAML 2.0 / JWT Bearer Auth Grant)
    "assertion",
    "Assertion",
    "ASSERTION",
    # SAML assertion in non-OAuth contexts
    "saml_assertion",
    "SAML-Assertion",
    "SamlAssertion",
    # JWT-bearer assertion variants
    "jwt_assertion",
    "subject_assertion",
])
def test_assertion_key_redaction(key: str) -> None:
    """RFC 7521-7523 `assertion` parameter carries a signed identity credential.

    Without this redaction, a SAML 2.0 / JWT bearer assertion logged as part of
    a request URL or error trace would expose the entire signed payload (user
    identity claims, attribute statements, replayable within validity window).
    """
    secret = "eyJraWQiOiJzaWctMTIzIn0.signedSamlOrJwtAssertionPayload.signature"
    msg = f"POST /token grant_type=urn:bearer {key}={secret}"
    sanitized = sanitize_log_message(msg)
    assert secret not in sanitized, f"{key} value leaked: {sanitized!r}"
    assert "***" in sanitized
