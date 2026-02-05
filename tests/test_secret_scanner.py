from __future__ import annotations

from pathlib import Path

from src.utils.secret_scanner import scan_repository


def test_secret_scanner_detects_high_entropy_string(tmp_path: Path) -> None:
    file_path = tmp_path / "config.txt"
    file_path.write_text("API_TOKEN = 'AbCdEfGh1234567890ijklMNOPQR'", encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Expected the scanner to flag the fake token"
    assert findings[0].path == file_path


def test_secret_scanner_ignores_short_placeholder(tmp_path: Path) -> None:
    file_path = tmp_path / "config.txt"
    file_path.write_text("API_TOKEN=demo", encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings == []


def test_secret_scanner_detects_quoted_secret_with_spaces(tmp_path: Path) -> None:
    file_path = tmp_path / "config.py"
    # A secret with ACTUAL spaces and sufficient entropy/length
    secret_value = "This Is A Long Secret With Spaces 123"
    content = f'API_TOKEN = "{secret_value}"'
    file_path.write_text(content, encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Scanner failed to detect quoted secret with spaces"
    # The scanner returns the match exactly as found by regex.
    # Our regex captures the content including quotes.
    # But _scan_line implementation: `candidate = match.group(2)`.
    # And we updated _scan_line to strip quotes.
    # Wait, findings[0].match in the original code:
    # `match=truncated` where `truncated` comes from `snippet` which comes from `_scan_line`.
    # `_scan_line` yields `(candidate, reason)`.
    # And we updated `_scan_line` to STRIP quotes from `candidate` before yielding.
    # So `findings[0].match` should be the stripped value!

    assert findings[0].match == secret_value


def test_secret_scanner_detects_aws_access_key(tmp_path: Path) -> None:
    file_path = tmp_path / "aws_creds.py"
    file_path.write_text('AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect AWS Access Key"
    assert "AKIAIOSFODNN7EXAMPLE" in [f.match for f in findings]
    assert "AWS Access Key ID gefunden" in [f.reason for f in findings]


def test_secret_scanner_detects_short_secret_assignment(tmp_path: Path) -> None:
    file_path = tmp_path / "config.py"
    # 20 chars, mixed case + digits -> should be detected with new threshold
    secret = "1234567890abcdef1234"
    file_path.write_text(f'api_key = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect 20-char secret assignment"
    assert findings[0].match == secret


def test_secret_scanner_detects_key_variable(tmp_path: Path) -> None:
    file_path = tmp_path / "keys.py"
    secret = "1234567890abcdef1234"
    file_path.write_text(f'private_key = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect variable named private_key"
    assert findings[0].match == secret


def test_secret_scanner_ignores_short_non_secret(tmp_path: Path) -> None:
    file_path = tmp_path / "ids.py"
    # Short ID (5 chars) assigned to sensitive-ish name
    file_path.write_text('my_id = "12345"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    # Should be ignored because length < 20
    assert findings == []


def test_secret_scanner_detects_secret_in_function_call(tmp_path: Path) -> None:
    file_path = tmp_path / "script.py"
    # High entropy string without assignment or colon
    secret = "AbCdEfGh1234567890ijklMNOPQR"
    file_path.write_text(f'connect("{secret}")', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect unassigned high-entropy secret"
    assert findings[0].match == secret
