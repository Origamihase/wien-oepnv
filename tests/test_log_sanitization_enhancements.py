import pytest
from src.utils.logging import sanitize_log_message

@pytest.mark.parametrize("key", [
    "passphrase",
    "my_passphrase",
    "db_passphrase",
    "aws_access_key_id",
    "aws_secret_access_key",
    "authorization_code",
    "auth_code",
])
def test_sensitive_keys_redaction(key):
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

    # Test Header style
    msg_header = f"{key}: {secret}"
    sanitized_header = sanitize_log_message(msg_header)
    assert secret not in sanitized_header
    assert "***" in sanitized_header

def test_passphrase_redaction_header():
    key = "Passphrase"
    secret = "secret123"
    msg = f"{key}: {secret}"
    sanitized = sanitize_log_message(msg)
    assert secret not in sanitized
    assert "***" in sanitized

@pytest.mark.parametrize("key", [
    "email",
    "user_email",
    "user.email",
    "contact_email",
    "email_address",
    "customer.email"
])
def test_email_redaction(key):
    secret = "user@example.com"
    # Test query param style
    msg = f"User logged in with {key}={secret}"
    sanitized = sanitize_log_message(msg)
    assert secret not in sanitized
    assert "***" in sanitized

    # Test JSON style
    msg_json = f'{{"{key}": "{secret}"}}'
    sanitized_json = sanitize_log_message(msg_json)
    assert secret not in sanitized_json
    assert "***" in sanitized_json
