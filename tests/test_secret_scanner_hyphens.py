
from __future__ import annotations

from pathlib import Path
from src.utils.secret_scanner import scan_repository

def test_secret_scanner_detects_hyphenated_keys(tmp_path: Path) -> None:
    file_path = tmp_path / "config.yaml"
    assignments = [
        ('api-key', "1234567890abcdef"),
        ('private-key', "1234567890abcdef"),
        ('client-id', "1234567890abcdef"),
        ('secret-key', "1234567890abcdef"),
        ('access-key', "1234567890abcdef"),
        ('auth-token', "1234567890abcdef"),
        ('ssh-key', "1234567890abcdef"),
        ('my-api-key', "1234567890abcdef"), # Prefix with hyphen
        ('api-key-v1', "1234567890abcdef"), # Suffix with hyphen
    ]

    content = ""
    for key, value in assignments:
        content += f'{key}: "{value}"\n'

    file_path.write_text(content, encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    # Expect all to be found
    findings_by_line = {}
    for f in findings:
        findings_by_line.setdefault(f.line_number, []).append(f)

    assert len(findings_by_line) == len(assignments), f"Expected findings on {len(assignments)} lines, got {len(findings_by_line)}"

    for line_num in range(1, len(assignments) + 1):
        assert line_num in findings_by_line, f"Line {line_num} ({assignments[line_num-1][0]}) not found"

def test_secret_scanner_detects_dot_separated_keys(tmp_path: Path) -> None:
    file_path = tmp_path / "config.properties"
    assignments = [
        ('api.key', "1234567890abcdef"),
        ('client.secret', "1234567890abcdef"),
        ('my.private.key', "1234567890abcdef"),
    ]

    content = ""
    for key, value in assignments:
        content += f'{key}={value}\n'

    file_path.write_text(content, encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    # Expect all to be found
    findings_by_line = {}
    for f in findings:
        findings_by_line.setdefault(f.line_number, []).append(f)

    assert len(findings_by_line) == len(assignments), f"Expected findings on {len(assignments)} lines, got {len(findings_by_line)}"

def test_secret_scanner_ignores_common_non_secrets(tmp_path: Path) -> None:
    file_path = tmp_path / "config.yaml"
    assignments = [
        ('image-url', "https://example.com/image.png"), # url not in list
        ('page-id', "12345"), # id not in list
        ('session-timeout', "3600"), # timeout not in list, session in list but 'timeout' suffix not allowed?
                                     # Wait, if prefix/suffix allows hyphen, session-timeout might match session?
                                     # 'session-timeout'. 'session' matches. Suffix '-timeout'.
                                     # If [a-z0-9_.-]* matches '-timeout', then it matches!
                                     # So session-timeout will be flagged if value looks like secret.
                                     # Value "3600" is too short/low entropy. So it should be ignored.
    ]

    content = ""
    for key, value in assignments:
        content += f'{key}: "{value}"\n'

    # Add one that looks like secret but key is safe
    content += 'my-safe-key: "AbCdEfGh1234567890ijklMNOPQR"\n'
    # my-safe-key contains 'key'? No. 'safe'? No.
    # So this should NOT be flagged as assignment.
    # BUT it might be flagged as HIGH ENTROPY string.

    file_path.write_text(content, encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    # Filter for assignment findings
    assignment_findings = [f for f in findings if "Verdächtige Zuweisung" in f.reason]

    assert len(assignment_findings) == 0, f"Found unexpected assignments: {[f.match for f in assignment_findings]}"
