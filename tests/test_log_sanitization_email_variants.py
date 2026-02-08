import pytest
from src.utils.logging import sanitize_log_message

@pytest.mark.parametrize("key", [
    "e-mail",
    "e_mail",
    "e.mail",
    "e mail",
    "user_e-mail",
    "customer.e-mail",
    "contact_e_mail"
])
def test_email_variants_redaction(key):
    secret = "user@example.com"
    # Test query param style
    msg = f"User logged in with {key}={secret}"
    sanitized = sanitize_log_message(msg)
    assert secret not in sanitized, f"Failed to redact {key} (Query Param)"
    assert "***" in sanitized

    # Test JSON style
    msg_json = f'{{"{key}": "{secret}"}}'
    sanitized_json = sanitize_log_message(msg_json)
    assert secret not in sanitized_json, f"Failed to redact {key} (JSON)"
    assert "***" in sanitized_json

    # Test Header style
    msg_header = f"{key}: {secret}"
    sanitized_header = sanitize_log_message(msg_header)
    assert secret not in sanitized_header, f"Failed to redact {key} (Header)"
    assert "***" in sanitized_header
