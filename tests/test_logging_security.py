import pytest
from src.utils.logging import sanitize_log_message

def test_sanitize_x_goog_api_key_header():
    """Verify that X-Goog-Api-Key headers are redacted in log messages."""
    secret = "AIzaSyD-SecretKey123"
    message = f"Sending request with header X-Goog-Api-Key: {secret}"
    sanitized = sanitize_log_message(message)
    assert secret not in sanitized
    assert "X-Goog-Api-Key: ***" in sanitized

def test_sanitize_x_api_key_header():
    """Verify that generic X-Api-Key headers are redacted."""
    secret = "secret-value"
    message = f"Header X-Api-Key: {secret}"
    sanitized = sanitize_log_message(message)
    assert secret not in sanitized
    assert "X-Api-Key: ***" in sanitized
