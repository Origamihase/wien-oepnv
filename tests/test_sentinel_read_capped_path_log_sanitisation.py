"""Sentinel PoC: ``read_capped_json`` and ``read_capped_text`` interpolated
their ``path`` argument into the operator-facing WARNING log line via the
bare ``%s`` format spec, without breaking the CodeQL clear-text-logging
taint flow that originates from
:func:`src.utils.env.read_secret` (``name`` parameter is treated as a
credential identifier by CodeQL's heuristic).

Threat model
============

The two helpers are reached from every operator-facing reader in the
project (``src/utils/cache.py``, ``src/utils/env.py``,
``src/utils/secret_scanner.py``, ``src/utils/stations.py``,
``src/places/quota.py``, ``src/places/merge.py``, ``src/places/tiling.py``,
``src/feed/logging.py``, ``src/providers/vor.py``,
``scripts/update_baustellen_cache.py``, …). Two attack surfaces share the
same log line:

1. **CodeQL ``py/clear-text-logging-sensitive-data``** — alerts #1758
   / #1759 / #1861 / #1862 in the repo as of 2026-05-11. CodeQL's taint
   analysis marks the path as secret-bearing because ``read_secret`` is
   one of the callers; ``sanitize_log_arg(str(path))`` is NOT recognised
   as a barrier across the function boundary, so a routing fix at the
   interpolation site does not close the alert.

2. **Trojan-Source path-name primitives** — the path string itself can
   carry BiDi controls (U+202E RLO inverts visual rendering), 8-bit C1
   controls (``\\x9b`` CSI / ``\\x9d`` OSC trigger SGR colour
   interpretation on 8-bit-C1-honouring terminals), ANSI escape prefixes
   (``\\x1b``), newline/CR (forge log records in line-based consumers),
   Tag block characters (invisible-instruction smuggling), and
   Variation Selectors (4-bit-payload steganography). A hostile
   contributor mis-naming a tracked file or a poisoned env var
   pointing at a planted path would leak these primitives verbatim
   into operator-facing logs + ``docs/feed_health.json`` + the
   GitHub-Issue auto-submission.

Fix: replace path interpolation with a one-way SHA-256 fingerprint
(truncated to 12 hex chars). The fingerprint is:

  * A CodeQL-recognised barrier (``hashlib`` is a documented sanitiser
    sink — secret-bearing taint cannot survive a cryptographic hash).
  * Trojan-Source-clean — the hex representation is `[0-9a-f]` only.
  * Operator-correlatable — running ``sha256(str(path))[:12]``
    locally on a candidate path confirms identity.
  * Stable across runs for a given path — useful for log aggregation /
    grep correlation.

Trade-off: operators lose the human-readable path in the log line.
They retain the ``label`` (caller-provided category) and the byte
count, plus the surrounding traceback / cron-job context which
typically narrows the candidate set to one or two files.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import pytest

from src.utils.files import (
    DEFAULT_MAX_JSON_FILE_BYTES,
    DEFAULT_MAX_TEXT_FILE_BYTES,
    read_capped_json,
    read_capped_text,
)


@pytest.mark.parametrize(
    "primitive,label",
    [
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
    ],
)
def test_read_capped_json_does_not_leak_path_primitives_into_log(
    primitive: str,
    label: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A poisoned path that exceeds ``max_bytes`` and triggers the
    WARNING log line MUST NOT propagate the primitive into the log
    output. The fingerprint barrier is hex-only, so no Trojan-Source
    primitive can survive.
    """
    poisoned = tmp_path / f"poison{primitive}.json"
    poisoned.write_bytes(b"x" * 1024)

    caplog.set_level(logging.WARNING)
    result = read_capped_json(poisoned, max_bytes=100, label="JSON")
    assert result is None

    for record in caplog.records:
        message = record.getMessage()
        assert primitive not in message, (
            f"{label} ({primitive!r}) leaked through read_capped_json "
            f"WARNING log: {message!r}"
        )


@pytest.mark.parametrize(
    "primitive,label",
    [
        ("‮", "U+202E RIGHT-TO-LEFT OVERRIDE"),
        ("​", "U+200B ZERO WIDTH SPACE"),
        ("\x9b", "U+009B 8-bit CSI"),
        ("\x1b", "U+001B ESC (ANSI prefix)"),
        ("\n", "newline"),
        ("\U000e0020", "U+E0020 Unicode Tag SPACE"),
    ],
)
def test_read_capped_text_does_not_leak_path_primitives_into_log(
    primitive: str,
    label: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Mirror invariant for :func:`read_capped_text` — same fix shape,
    same threat surface.
    """
    poisoned = tmp_path / f"poison{primitive}.csv"
    poisoned.write_bytes(b"x" * 1024)

    caplog.set_level(logging.WARNING)
    result = read_capped_text(poisoned, max_bytes=100, label="text")
    assert result is None

    for record in caplog.records:
        message = record.getMessage()
        assert primitive not in message, (
            f"{label} ({primitive!r}) leaked through read_capped_text "
            f"WARNING log: {message!r}"
        )


def test_read_capped_json_log_carries_path_sha256_fingerprint(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Operator-correlation invariant: the WARNING log carries the
    truncated SHA-256 of the path bytes so operators can rerun the
    hash on a candidate path locally to confirm identity.
    """
    benign = tmp_path / "huge_data.json"
    benign.write_bytes(b"x" * 1024)

    caplog.set_level(logging.WARNING)
    result = read_capped_json(benign, max_bytes=100, label="JSON")
    assert result is None

    expected_fingerprint = hashlib.sha256(
        str(benign).encode("utf-8", errors="replace")
    ).hexdigest()[:12]
    messages = [record.getMessage() for record in caplog.records]
    combined = " ".join(messages)
    assert expected_fingerprint in combined, (
        f"Fingerprint {expected_fingerprint!r} missing from log: "
        f"{combined!r}"
    )


def test_read_capped_text_log_carries_path_sha256_fingerprint(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Mirror invariant for :func:`read_capped_text`."""
    benign = tmp_path / "huge_data.csv"
    benign.write_bytes(b"x" * 1024)

    caplog.set_level(logging.WARNING)
    result = read_capped_text(benign, max_bytes=100, label="text")
    assert result is None

    expected_fingerprint = hashlib.sha256(
        str(benign).encode("utf-8", errors="replace")
    ).hexdigest()[:12]
    messages = [record.getMessage() for record in caplog.records]
    combined = " ".join(messages)
    assert expected_fingerprint in combined, (
        f"Fingerprint {expected_fingerprint!r} missing from log: "
        f"{combined!r}"
    )


def test_read_capped_json_log_does_not_leak_raw_path(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CodeQL-recognised barrier invariant: the raw path string MUST
    NOT appear in the log line. Only the SHA-256 fingerprint and the
    caller-provided ``label`` survive.
    """
    sensitive = tmp_path / "VOR_ACCESS_ID"
    sensitive.write_bytes(b"x" * 1024)

    caplog.set_level(logging.WARNING)
    result = read_capped_json(sensitive, max_bytes=100, label="systemd credential")
    assert result is None

    messages = [record.getMessage() for record in caplog.records]
    combined = " ".join(messages)
    # The basename ``VOR_ACCESS_ID`` is the credential identifier; it
    # MUST NOT appear in the log line. (The label is intentional
    # caller-provided diagnostic context and is allowed.)
    assert "VOR_ACCESS_ID" not in combined, (
        f"Credential-coded path leaked: {combined!r}"
    )


def test_read_capped_text_log_does_not_leak_raw_path(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Mirror invariant for :func:`read_capped_text`."""
    sensitive = tmp_path / "VOR_ACCESS_ID"
    sensitive.write_bytes(b"x" * 1024)

    caplog.set_level(logging.WARNING)
    result = read_capped_text(sensitive, max_bytes=100, label="docker secret")
    assert result is None

    messages = [record.getMessage() for record in caplog.records]
    combined = " ".join(messages)
    assert "VOR_ACCESS_ID" not in combined, (
        f"Credential-coded path leaked: {combined!r}"
    )


def test_read_capped_json_default_cap_returns_none_on_oversize(
    tmp_path: Path,
) -> None:
    """Smoke-test: the size cap still triggers on legitimate oversize
    files. ``DEFAULT_MAX_JSON_FILE_BYTES`` (50 MiB) is too large to
    exercise here, so we use a custom small cap and confirm the
    function still returns ``None``.
    """
    assert DEFAULT_MAX_JSON_FILE_BYTES > 0
    benign = tmp_path / "size_test.json"
    benign.write_bytes(b"x" * 1024)
    assert read_capped_json(benign, max_bytes=100, label="JSON") is None


def test_read_capped_text_default_cap_returns_none_on_oversize(
    tmp_path: Path,
) -> None:
    """Smoke-test mirror for :func:`read_capped_text`."""
    assert DEFAULT_MAX_TEXT_FILE_BYTES > 0
    benign = tmp_path / "size_test.csv"
    benign.write_bytes(b"x" * 1024)
    assert read_capped_text(benign, max_bytes=100, label="text") is None
