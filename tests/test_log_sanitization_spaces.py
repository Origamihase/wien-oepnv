
import pytest
from src.utils.logging import sanitize_log_message

def test_sanitization_spaces_assignment():
    """Test that assignments with spaces around '=' are redacted."""
    msg = 'password = "secret123"'
    sanitized = sanitize_log_message(msg)
    assert "***" in sanitized
    assert "secret123" not in sanitized

def test_sanitization_spaces_assignment_unquoted():
    """Test that unquoted assignments with spaces are redacted."""
    msg = "password = secret123"
    sanitized = sanitize_log_message(msg)
    assert "***" in sanitized
    assert "secret123" not in sanitized

def test_sanitization_spaces_assignment_multiline():
    """Test multiline assignment with spaces."""
    msg = "password \n = \n secret123"
    sanitized = sanitize_log_message(msg)
    assert "secret123" not in sanitized

def test_sanitization_spaces_assignment_key_value_pairs():
    """Test space separated key-value pairs with spaces in assignment."""
    # Use 'token' which is in _keys
    msg = "password = secret123 token = foo"
    sanitized = sanitize_log_message(msg)
    assert "secret123" not in sanitized
    assert "foo" not in sanitized
    assert sanitized == "password = *** token = ***"
