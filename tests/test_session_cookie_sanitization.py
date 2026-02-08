
import pytest
from src.utils.http import _sanitize_url_for_error
from src.utils.logging import sanitize_log_message

def test_http_sanitize_session_id():
    url = "http://example.com?session_id=secret123"
    sanitized = _sanitize_url_for_error(url)
    assert "secret" not in sanitized
    assert "%2A%2A%2A" in sanitized or "***" in sanitized

def test_http_sanitize_sessionid():
    url = "http://example.com?sessionid=secret123"
    sanitized = _sanitize_url_for_error(url)
    assert "secret" not in sanitized
    assert "%2A%2A%2A" in sanitized or "***" in sanitized

def test_http_sanitize_cookie():
    url = "http://example.com?cookie=secret123"
    sanitized = _sanitize_url_for_error(url)
    assert "secret" not in sanitized
    assert "%2A%2A%2A" in sanitized or "***" in sanitized

def test_logging_sanitize_session_id():
    msg = "session_id=secret123"
    sanitized = sanitize_log_message(msg)
    assert "secret" not in sanitized
    assert "***" in sanitized

def test_logging_sanitize_session_id_with_space():
    msg = "session id=secret123"
    sanitized = sanitize_log_message(msg)
    assert "secret" not in sanitized
    assert "***" in sanitized

def test_logging_sanitize_cookie():
    msg = "cookie=secret123"
    sanitized = sanitize_log_message(msg)
    assert "secret" not in sanitized
    assert "***" in sanitized

def test_logging_sanitize_json_session_id():
    msg = '{"session_id": "secret123"}'
    sanitized = sanitize_log_message(msg)
    assert "secret" not in sanitized
    assert "***" in sanitized

def test_logging_sanitize_json_cookie():
    msg = '{"cookie": "secret123"}'
    sanitized = sanitize_log_message(msg)
    assert "secret" not in sanitized
    assert "***" in sanitized
