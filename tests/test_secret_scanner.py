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
