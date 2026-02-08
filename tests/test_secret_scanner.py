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

    # Ensure full secret is NOT in findings (Redaction check)
    assert secret_value not in [f.match for f in findings]

    # Check for redacted match: first 4 + *** + last 4
    expected = f"{secret_value[:4]}***{secret_value[-4:]}"
    assert findings[0].match == expected


def test_secret_scanner_detects_aws_access_key(tmp_path: Path) -> None:
    file_path = tmp_path / "aws_creds.py"
    secret_value = "AKIAIOSFODNN7EXAMPLE"
    file_path.write_text(f'AWS_ACCESS_KEY_ID = "{secret_value}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect AWS Access Key"

    # Ensure full secret is NOT in findings (Redaction check)
    assert secret_value not in [f.match for f in findings]

    # Length 20 -> 2 chars
    expected = f"{secret_value[:2]}***{secret_value[-2:]}"
    assert expected in [f.match for f in findings]
    assert "AWS Access Key ID gefunden" in [f.reason for f in findings]


def test_secret_scanner_detects_short_secret_assignment(tmp_path: Path) -> None:
    file_path = tmp_path / "config.py"
    # 20 chars, mixed case + digits -> should be detected with new threshold
    secret = "1234567890abcdef1234"
    file_path.write_text(f'api_key = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect 20-char secret assignment"
    assert secret not in [f.match for f in findings]
    # Length 20 -> 2 chars
    expected = f"{secret[:2]}***{secret[-2:]}"
    assert findings[0].match == expected


def test_secret_scanner_detects_key_variable(tmp_path: Path) -> None:
    file_path = tmp_path / "keys.py"
    secret = "1234567890abcdef1234"
    file_path.write_text(f'private_key = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect variable named private_key"
    assert secret not in [f.match for f in findings]
    # Length 20 -> 2 chars
    expected = f"{secret[:2]}***{secret[-2:]}"
    assert findings[0].match == expected


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
    assert secret not in [f.match for f in findings]
    expected = f"{secret[:4]}***{secret[-4:]}"
    assert findings[0].match == expected


def test_secret_scanner_detects_long_lowercase_assignment(tmp_path: Path) -> None:
    file_path = tmp_path / "config.py"
    # Long lowercase secret (single category) assigned to sensitive variable
    secret = "abcdefghijklmnopqrstuvwxyzabcdefgh"
    file_path.write_text(f'API_KEY = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect long lowercase secret in assignment"
    assert secret not in [f.match for f in findings]
    expected = f"{secret[:4]}***{secret[-4:]}"
    assert findings[0].match == expected


def test_secret_scanner_detects_credential_assignment(tmp_path: Path) -> None:
    file_path = tmp_path / "creds.py"
    # 20 chars, mixed case + digits -> should be detected with new threshold
    secret = "credential_is_20chars"
    file_path.write_text(f'my_credential = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect 20-char credential assignment"
    assert secret not in [f.match for f in findings]
    expected = f"{secret[:4]}***{secret[-4:]}"
    assert findings[0].match == expected


def test_secret_scanner_detects_passphrase_assignment(tmp_path: Path) -> None:
    file_path = tmp_path / "wifi.py"
    # 21 chars
    secret = "passphrase_is_21chars"
    file_path.write_text(f'wifi_passphrase = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect passphrase assignment"
    assert secret not in [f.match for f in findings]
    expected = f"{secret[:4]}***{secret[-4:]}"
    assert findings[0].match == expected

def test_secret_scanner_detects_short_password_assignment(tmp_path: Path) -> None:
    file_path = tmp_path / "creds.py"
    # 10 chars, explicitly assigned to 'password'
    secret = "Pass1234!!"
    file_path.write_text(f'password = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect 10-char password assignment"
    assert secret not in [f.match for f in findings]
    # Length 10 -> 2 chars
    expected = f"{secret[:2]}***{secret[-2:]}"
    assert findings[0].match == expected

def test_secret_scanner_detects_extended_keys(tmp_path: Path) -> None:
    file_path = tmp_path / "extended.py"
    # Create secrets for new keys
    assignments = [
        ('accesskey', "AccessKey1234567890"),
        ('client_id', "ClientId1234567890"),
        ('bearer', "BearerToken1234567890"),
        ('session_id', "SessionId1234567890"),
        ('signature', "Signature1234567890"),
        ('jwt', "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"),
        ('cookie', "JSESSIONID=1234567890abcdef")
    ]

    content = ""
    for key, value in assignments:
        content += f'{key} = "{value}"\n'

    file_path.write_text(content, encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    # We expect at least one finding per assignment.
    # Some secrets (like JWT or long cookies) might also trigger the high-entropy check,
    # resulting in multiple findings for the same line.
    findings_by_line = {}
    for f in findings:
        findings_by_line.setdefault(f.line_number, []).append(f)

    assert len(findings_by_line) == len(assignments), f"Expected findings on {len(assignments)} lines, got {len(findings_by_line)}"

    for line_findings in findings_by_line.values():
        # At least one finding on the line should be the assignment check
        reasons = [f.reason for f in line_findings]
        assert any("Verdächtige Zuweisung" in r for r in reasons), f"Line missed assignment check: {reasons}"

        # Check redaction
        for finding in line_findings:
            assert "***" in finding.match
            # Check that none of the raw secrets are in the match string
            for _, val in assignments:
                assert val not in finding.match

def test_secret_scanner_detects_unquoted_secret_with_spaces(tmp_path: Path) -> None:
    file_path = tmp_path / "config.ini"
    # Unquoted secret with spaces. Use >20 chars to trigger 4-char redaction.
    secret_value = "my secret key 123456789"
    content = f'api_key = {secret_value}'
    file_path.write_text(content, encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect unquoted secret with spaces"
    assert secret_value not in [f.match for f in findings]
    # Check for correct redaction: first 4 + *** + last 4
    expected = f"{secret_value[:4]}***{secret_value[-4:]}"
    assert findings[0].match == expected
