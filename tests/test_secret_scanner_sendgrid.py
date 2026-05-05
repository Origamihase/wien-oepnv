"""Tests for SendGrid API key detection in the secret scanner.

SendGrid keys use the format ``SG.<22 chars>.<43 chars>``. The dots between
segments fall outside the generic high-entropy character class
(``[A-Za-z0-9+/=_-]``), so without a dedicated pattern only the trailing
43-character segment is caught (and as a generic "high-entropy" finding,
not specifically attributed to SendGrid). This module verifies that the
specific pattern catches the full token regardless of context.
"""
from __future__ import annotations

from pathlib import Path

from src.utils.secret_scanner import scan_repository


def _make_token() -> str:
    # 22 + 43 chars in the documented base64url-style alphabet.
    middle = "AbCdEfGh1234567890abCD"  # 22 chars
    tail = "ef1234567890ABCDEFGHIJKLMNOPQRSTUVWXYZ12345"  # 43 chars
    assert len(middle) == 22 and len(tail) == 43
    return f"SG.{middle}.{tail}"


def test_secret_scanner_detects_sendgrid_api_key_assignment(tmp_path: Path) -> None:
    file_path = tmp_path / "mailer.py"
    secret = _make_token()
    file_path.write_text(f'SENDGRID_API_KEY = "{secret}"\n', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect SendGrid API Key in sensitive assignment"
    assert secret not in [f.match for f in findings]
    reasons = [f.reason for f in findings]
    assert "SendGrid API Key gefunden" in reasons, reasons


def test_secret_scanner_detects_sendgrid_in_function_call(tmp_path: Path) -> None:
    """Without a specific pattern, only the trailing 43-char segment was flagged.

    Verify the full token is caught and attributed correctly even when there is no
    sensitive variable name to trigger the generic assignment detector.
    """
    file_path = tmp_path / "mailer.py"
    secret = _make_token()
    file_path.write_text(f'connect("{secret}")\n', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    sendgrid_findings = [f for f in findings if f.reason == "SendGrid API Key gefunden"]
    assert sendgrid_findings, f"Should detect SendGrid API Key. Got: {findings}"
    # Raw secret must never appear unredacted in findings.
    assert secret not in [f.match for f in findings]


def test_secret_scanner_sendgrid_match_is_redacted(tmp_path: Path) -> None:
    file_path = tmp_path / "mailer.py"
    secret = _make_token()
    file_path.write_text(f'SENDGRID = "{secret}"\n', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    sendgrid_findings = [f for f in findings if f.reason == "SendGrid API Key gefunden"]
    assert sendgrid_findings
    # Length 69 -> first 4 + *** + last 4
    expected = f"{secret[:4]}***{secret[-4:]}"
    assert sendgrid_findings[0].match == expected
