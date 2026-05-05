"""Tests for GitHub OAuth/App/Refresh token detection in the secret scanner.

These complement test_secret_scanner_priority.py (which covers ``ghp_``) by
verifying the remaining GitHub token formats (``gho_``, ``ghu_``, ``ghs_``,
``ghr_``). Of these, ``ghs_`` is the format of ``GITHUB_TOKEN`` auto-injected
into GitHub Actions runners — leakage there grants repo-scoped access until
the workflow ends.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.utils.secret_scanner import scan_repository


@pytest.mark.parametrize(
    ("prefix", "expected_reason"),
    [
        ("gho_", "GitHub OAuth Access Token gefunden"),
        ("ghu_", "GitHub App User-to-Server Token gefunden"),
        ("ghs_", "GitHub App Server-to-Server Token gefunden"),
        ("ghr_", "GitHub Refresh Token gefunden"),
    ],
)
def test_secret_scanner_detects_github_token_variants(
    tmp_path: Path, prefix: str, expected_reason: str
) -> None:
    file_path = tmp_path / f"config_{prefix.rstrip('_')}.py"
    secret = prefix + "1234567890abcdefghijklmnopqrstuvwxyz"
    assert len(secret) == 40
    file_path.write_text(f'TOKEN = "{secret}"\n', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, f"Should detect {prefix} token"
    # Raw secret must never appear unredacted in findings.
    assert secret not in [f.match for f in findings]
    reasons = [f.reason for f in findings]
    assert expected_reason in reasons, reasons


def test_secret_scanner_ghs_token_takes_priority_over_generic_assignment(
    tmp_path: Path,
) -> None:
    """Specific ``ghs_`` reason should win over the generic assignment match."""
    file_path = tmp_path / "ci.py"
    token = "ghs_" + "A" * 36
    file_path.write_text(f'GITHUB_TOKEN = "{token}"\n', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert len(findings) == 1
    assert findings[0].reason == "GitHub App Server-to-Server Token gefunden"
    assert token not in findings[0].match
