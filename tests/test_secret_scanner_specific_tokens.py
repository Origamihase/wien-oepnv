from __future__ import annotations

from pathlib import Path
from src.utils.secret_scanner import scan_repository

def test_secret_scanner_detects_google_api_key(tmp_path: Path) -> None:
    file_path = tmp_path / "config.py"
    # Valid Google API Key (39 chars, starts with AIza)
    # AIza + 35 chars
    secret = "AIzaSyD-1234567890abcdefghijklmnopqrstu"
    file_path.write_text(f'google_api_key = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect Google API Key"
    assert secret not in [f.match for f in findings]

    reasons = [f.reason for f in findings]
    print(f"DEBUG: Found Google API Key reasons: {reasons}")
    assert "Google API Key gefunden" in reasons


def test_secret_scanner_detects_telegram_bot_token(tmp_path: Path) -> None:
    file_path = tmp_path / "telegram.py"
    # Valid-looking Telegram Bot Token (ID:Token)
    # ID: 3-14 digits, Token: 35 chars
    secret = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ123456789"
    file_path.write_text(f'telegram_token = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect Telegram Bot Token"
    assert secret not in [f.match for f in findings]

    reasons = [f.reason for f in findings]
    print(f"DEBUG: Found Telegram Bot Token reasons: {reasons}")
    assert "Telegram Bot Token gefunden" in reasons
