
import pytest
from unittest.mock import MagicMock
from src.providers import vor

def test_log_warning_sanitizes_newlines(monkeypatch):
    mock_logger = MagicMock()
    monkeypatch.setattr(vor, "log", mock_logger)

    # Attack string with newline and carriage return
    attack = "station_id\n[INFO] Fake Log Entry\r\n"

    # We expect _log_warning to sanitize the arguments passed to it
    vor._log_warning("Message: %s", attack)

    # Verify call arguments
    assert mock_logger.warning.called
    call_args = mock_logger.warning.call_args
    # call_args[0] contains positional args: (msg, *args)
    # The first arg is the format string
    # The subsequent args are the values to be formatted

    formatted_args = call_args[0][1:]
    assert len(formatted_args) == 1
    sanitized = formatted_args[0]

    # Check that raw newlines are removed/escaped
    assert "\n" not in sanitized
    assert "\r" not in sanitized
    assert "\\n" in sanitized or " " in sanitized

    # Check that the content is still preserved (escaped)
    assert "station_id" in sanitized
    assert "Fake Log Entry" in sanitized

def test_sanitize_message_strips_control_chars():
    # Direct test of _sanitize_message
    text = "Line 1\nLine 2\rLine 3\tTabbed"
    sanitized = vor._sanitize_message(text)

    assert "\n" not in sanitized
    assert "\r" not in sanitized
    assert "\t" not in sanitized
    assert "Line 1\\nLine 2\\rLine 3\\tTabbed" in sanitized

def test_sanitize_message_strips_ansi_codes():
    # Test for ANSI escape codes (e.g. colors)
    red = "\x1b[31m"
    reset = "\x1b[0m"
    msg = f"Error {red}Critical{reset} failure"
    sanitized = vor._sanitize_message(msg)

    assert "\x1b" not in sanitized
    assert "Critical" in sanitized
    # The control character \x1b and its parameters are removed completely
    assert "[31m" not in sanitized
    assert sanitized == "Error Critical failure"
