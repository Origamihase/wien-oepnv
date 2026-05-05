from pathlib import Path

from src.utils.logging import sanitize_log_message
from src.utils.secret_scanner import scan_repository


def test_sanitize_log_message_escaped_quotes() -> None:
    """Verify that sanitize_log_message handles escaped quotes correctly."""
    # We use spaces to ensure the regex relies on the quoted part, not the fallback [^&\s]+
    secret = 'token="secret \\" leak"'
    sanitized = sanitize_log_message(secret)

    # We expect full redaction
    assert "leak" not in sanitized
    assert "secret" not in sanitized
    assert "token=***" in sanitized


def test_sanitize_log_message_single_escaped_quotes() -> None:
    """Verify that sanitize_log_message handles escaped single quotes correctly."""
    secret = "token='secret \\' leak'"
    sanitized = sanitize_log_message(secret)

    assert "leak" not in sanitized
    assert "secret" not in sanitized
    assert "token=***" in sanitized


def test_secret_scanner_escaped_quotes(tmp_path: Path) -> None:
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
    # The scanner masks the secret: 1234***leak
    # secret_inner is 12345678901234567890\"leak (24 chars)
    # So it keeps first 4 ("1234") and last 4 ("leak")
    assert match_str == "1234***leak"
