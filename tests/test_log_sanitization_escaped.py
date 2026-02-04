import sys
import logging
import pytest
from pathlib import Path
from unittest.mock import patch

from src.utils.logging import sanitize_log_message
from src.utils.secret_scanner import scan_repository

# -----------------------------------------------------------------------------
# Fixture for Env Fallback
# -----------------------------------------------------------------------------

@pytest.fixture
def fallback_env_module():
    """
    Fixture that forces src.utils.env to be imported in fallback mode
    (simulating missing src.utils.logging).
    """
    # Preserve original modules
    original_env = sys.modules.get("src.utils.env")
    original_logging = sys.modules.get("src.utils.logging")

    # Clean up existing modules to ensure fresh import
    if "src.utils.env" in sys.modules:
        del sys.modules["src.utils.env"]
    if "src.utils.logging" in sys.modules:
        del sys.modules["src.utils.logging"]

    # Simulate utils.logging being missing
    with patch.dict(sys.modules, {"src.utils.logging": None, "utils.logging": None}):
        import src.utils.env as env
        yield env

    # Restore original modules or clean up
    if "src.utils.env" in sys.modules:
        del sys.modules["src.utils.env"]
    if original_env:
        sys.modules["src.utils.env"] = original_env

    if "src.utils.logging" in sys.modules:
        del sys.modules["src.utils.logging"]
    if original_logging:
        sys.modules["src.utils.logging"] = original_logging


# -----------------------------------------------------------------------------
# Tests for Logging (Main)
# -----------------------------------------------------------------------------

def test_sanitize_log_message_escaped_quotes():
    """Verify that sanitize_log_message handles escaped quotes correctly."""
    # We use spaces to ensure the regex relies on the quoted part, not the fallback [^&\s]+
    secret = 'token="secret \\" leak"'
    sanitized = sanitize_log_message(secret)

    # We expect full redaction
    assert "leak" not in sanitized
    assert "secret" not in sanitized
    assert "token=***" in sanitized


def test_sanitize_log_message_single_escaped_quotes():
    """Verify that sanitize_log_message handles escaped single quotes correctly."""
    secret = "token='secret \\' leak'"
    sanitized = sanitize_log_message(secret)

    assert "leak" not in sanitized
    assert "secret" not in sanitized
    assert "token=***" in sanitized


# -----------------------------------------------------------------------------
# Tests for Env Fallback
# -----------------------------------------------------------------------------

def test_fallback_env_escaped_quotes(fallback_env_module, caplog):
    """Verify that env fallback logging handles escaped quotes correctly."""
    caplog.set_level(logging.WARNING, logger="build_feed")

    sensitive_value = 'token="secret \\" leak"'

    # Trigger a warning which calls sanitize_log_message on the value
    # We use get_int_env with a non-int string to trigger warning
    with patch("os.getenv", return_value=sensitive_value):
        fallback_env_module.get_int_env("DUMMY_VAR", 42)

    # Check logs
    assert "leak" not in caplog.text
    assert "secret" not in caplog.text
    assert "token=***" in caplog.text


# -----------------------------------------------------------------------------
# Tests for Secret Scanner
# -----------------------------------------------------------------------------

def test_secret_scanner_escaped_quotes(tmp_path):
    """Verify that secret scanner captures the FULL secret including escaped quotes."""
    file_path = tmp_path / "config.py"
    # A long secret with escaped quotes
    # 24 chars to pass checks: 12345678901234567890\"leak
    secret_inner = '12345678901234567890\\"leak'
    content = f'API_TOKEN = "{secret_inner}"'
    file_path.write_text(content, encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert len(findings) > 0
    # The match should contain the full secret string
    # If the regex stops early at \", it will be truncated
    match_str = findings[0].match

    assert "leak" in match_str
    assert match_str == secret_inner
