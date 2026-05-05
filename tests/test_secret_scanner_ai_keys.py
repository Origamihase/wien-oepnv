"""Tests for detection of modern AI provider API keys (Anthropic, OpenAI)."""
from __future__ import annotations

from pathlib import Path

from src.utils.secret_scanner import scan_repository


def test_secret_scanner_detects_anthropic_api_key(tmp_path: Path) -> None:
    file_path = tmp_path / "config.py"
    # Realistic Anthropic API key: sk-ant-api03-<93 chars>AA
    secret = (
        "sk-ant-api03-AaBbCcDdEeFfGgHhIiJjKkLlMmNn"
        "OoPpQqRrSsTtUuVvWwXxYyZz0123456789-_aA-bB"
        "0123456789abcdefAA"
    )
    file_path.write_text(f'ANTHROPIC_API_KEY = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect Anthropic API Key"
    # Ensure raw secret never appears in findings (redaction)
    assert secret not in [f.match for f in findings]
    reasons = [f.reason for f in findings]
    assert "Anthropic API Key gefunden" in reasons


def test_secret_scanner_detects_anthropic_admin_key(tmp_path: Path) -> None:
    file_path = tmp_path / "admin.py"
    secret = (
        "sk-ant-admin01-Z9Y8X7W6V5U4T3S2R1Q0POMLK"
        "JIHGFEDCBA0123456789-_abcdefghijklmnopqrs"
        "tuvwxyz0123456789AA"
    )
    file_path.write_text(f'ADMIN_KEY = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect Anthropic Admin key"
    assert secret not in [f.match for f in findings]
    reasons = [f.reason for f in findings]
    assert "Anthropic API Key gefunden" in reasons


def test_secret_scanner_detects_openai_project_key(tmp_path: Path) -> None:
    file_path = tmp_path / "config.py"
    # OpenAI project keys start with sk-proj- and are long alphanumeric/underscore/hyphen strings.
    secret = "sk-proj-" + "A" * 40 + "BcDeFgHiJk"
    file_path.write_text(f'OPENAI_API_KEY = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect OpenAI Project API key"
    assert secret not in [f.match for f in findings]
    reasons = [f.reason for f in findings]
    assert "OpenAI Project API Key gefunden" in reasons


def test_secret_scanner_detects_openai_service_account_key(tmp_path: Path) -> None:
    file_path = tmp_path / "svc.py"
    secret = "sk-svcacct-" + "1B" * 25 + "abcDEF"
    file_path.write_text(f'svc_token = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect OpenAI Service Account key"
    assert secret not in [f.match for f in findings]
    reasons = [f.reason for f in findings]
    assert "OpenAI Service Account Key gefunden" in reasons


def test_secret_scanner_detects_openai_legacy_key(tmp_path: Path) -> None:
    file_path = tmp_path / "legacy.py"
    # Legacy OpenAI key is sk- followed by exactly 48 alphanumeric chars (51 total).
    secret = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0U1v2W3x4"
    assert len(secret) == 51
    file_path.write_text(f'openai_api_key = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect legacy OpenAI key"
    assert secret not in [f.match for f in findings]
    reasons = [f.reason for f in findings]
    assert "OpenAI API Key gefunden" in reasons
