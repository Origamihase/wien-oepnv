"""Hilfen und Fixtures für die Tests in diesem Paket.

Früher in tests/test_sentinel_path_log_sanitisation_scripts_round2.py.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest


_PRIMITIVES: list[tuple[str, str]] = [
    ("‮", "U+202E RIGHT-TO-LEFT OVERRIDE"),
    ("​", "U+200B ZERO WIDTH SPACE"),
    ("\x9b", "U+009B 8-bit CSI"),
    ("\x9d", "U+009D 8-bit OSC"),
    ("\x1b", "U+001B ESC (ANSI prefix)"),
    ("\x07", "U+0007 BEL"),
    ("\n", "newline (record terminator)"),
    ("\r", "carriage return"),
    ("\U000e0020", "U+E0020 Unicode Tag SPACE"),
    ("︀", "U+FE00 VARIATION SELECTOR-1"),
]


def _fingerprint(path: Path) -> str:
    """Return the canonical 12-hex SHA-256 fingerprint of *path*."""
    return hashlib.sha256(
        str(path).encode("utf-8", errors="replace")
    ).hexdigest()[:12]


def _poisoned_path(tmp_path: Path, primitive: str, filename: str) -> Path:
    """Return a path under a directory whose name carries ``primitive``."""
    poisoned_dir = tmp_path / f"dir{primitive}sub"
    poisoned_dir.mkdir(parents=True, exist_ok=True)
    return poisoned_dir / filename


def _assert_primitive_absent(
    caplog: pytest.LogCaptureFixture,
    primitive: str,
    primitive_label: str,
    site_label: str,
) -> None:
    """Assert no captured log record carries the primitive verbatim."""
    for record in caplog.records:
        message = record.getMessage()
        assert primitive not in message, (
            f"{primitive_label} ({primitive!r}) leaked through "
            f"{site_label}: {message!r}"
        )
        for arg in record.args or ():
            assert primitive not in str(arg), (
                f"{primitive_label} ({primitive!r}) leaked through "
                f"{site_label} log args: {arg!r}"
            )


__all__ = [
    "_PRIMITIVES",
    "_assert_primitive_absent",
    "_fingerprint",
    "_poisoned_path",
]
