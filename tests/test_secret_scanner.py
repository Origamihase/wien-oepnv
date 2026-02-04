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
