"""Sentinel PoC: ``read_capped_json`` and ``read_capped_text`` interpolated
their ``path`` argument into the operator-facing WARNING log line via the
bare ``%s`` format spec, without routing it through the canonical log
sanitiser :func:`src.utils.logging.sanitize_log_arg`.

Threat model
============

The two helpers are reached from every operator-facing reader in the
project (``src/utils/cache.py``, ``src/utils/env.py``,
``src/utils/secret_scanner.py``, ``src/utils/stations.py``,
``src/places/quota.py``, ``src/places/merge.py``, ``src/places/tiling.py``,
``src/feed/logging.py``, ``src/providers/vor.py``,
``scripts/update_baustellen_cache.py``, …). The ``path`` argument is
constructed from operator-controlled state — env vars (``CREDENTIALS_DIRECTORY``,
``WIEN_OEPNV_ENV_FILES``, ``STATIONS_FILE``, ``VOR_STATION_IDS_DEFAULT``,
``MAPPING_FILE``), the systemd / Docker secrets directory
(``/run/secrets/X``), and ``subprocess`` output (``git ls-files -z`` paths
in the secret scanner).

Pre-fix shape:

* The path string itself can carry **Trojan-Source / BiDi / control /
  ANSI-escape** primitives (a hostile contributor mis-naming a tracked
  file, a poisoned env-var pointing at a planted path with embedded
  ``\\x9b...m`` SGR colour sequences, an operator typo that introduces
  an invisible-tag-character variant). The pre-fix log line interpolated
  the raw path bytes into ``log.warning`` → ``docs/feed_health.json``
  (public artefact) → the GitHub-Issue auto-submission body.

* CodeQL's ``py/clear-text-logging-sensitive-data`` taint analysis
  flagged the call site (alerts #1758, #1759 in repo as of 2026-05-11)
  because the path string flows from credential-bearing sources.

Post-fix the path is routed through :func:`sanitize_log_arg`, which:

1. Strips the canonical CVE-2021-42574 Trojan-Source primitive union
   (BiDi controls, zero-width chars, line-terminator separators, 8-bit
   C1 controls, DEL, ANSI escapes, plus — post Round 15 — the Unicode
   Tag block and Variation Selectors).
2. Defangs newlines / CR / TAB (escapes them as ``\\n`` / ``\\r`` /
   ``\\t`` literals) so the path cannot inject a forged record into
   downstream log consumers.
3. Acts as a CodeQL barrier for the clear-text-logging taint
   propagation: the sanitiser produces a fresh string whose taint is
   broken in the dataflow graph.

The two PoC paths below are the canonical attack shapes:

* ``data/secrets\\x9b31m_planted_path.json`` — 8-bit CSI SGR primitive in
  a poisoned path name. Pre-fix the bytes land in the cron-pipeline log;
  on a 8-bit-C1-honouring terminal they trigger SGR colour
  interpretation.
* ``data/poisoned\\u202epath.json`` — U+202E RIGHT-TO-LEFT OVERRIDE in a
  path name. Pre-fix the path reverses visually in any BiDi-aware
  renderer (terminal, GitHub Issue body, RSS reader if the path is
  echoed into a feed item description as part of a downstream error
  message).
"""
from __future__ import annotations

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
def test_read_capped_json_strips_path_primitives_from_log_line(
    primitive: str,
    label: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A poisoned path that exceeds ``max_bytes`` and triggers the
    WARNING log line MUST NOT propagate the primitive into the log
    output. The canonical log sanitiser strips Trojan-Source / BiDi /
    control / ANSI / Tag / VS primitives at the interpolation
    boundary.
    """
    poisoned = tmp_path / f"poison{primitive}.json"
    poisoned.write_bytes(b"x" * 1024)

    caplog.set_level(logging.WARNING)
    result = read_capped_json(poisoned, max_bytes=100, label="JSON")
    assert result is None

    # The primitive itself must not appear verbatim in any captured
    # log record. Newline / CR / TAB are explicitly escaped (and that
    # escape behaviour is the canonical sanitiser contract).
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
def test_read_capped_text_strips_path_primitives_from_log_line(
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


def test_read_capped_json_default_cap_path_log_preserves_filename(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Regression: the sanitiser MUST keep the path's printable
    characters intact for legitimate operator-facing diagnostics. A
    benign path name with ASCII + German umlauts survives unchanged.
    """
    benign = tmp_path / "Größe_Test_Wien.json"
    benign.write_bytes(b"x" * (DEFAULT_MAX_JSON_FILE_BYTES + 1))

    caplog.set_level(logging.WARNING)
    result = read_capped_json(benign, max_bytes=100, label="JSON")
    assert result is None

    # The legitimate filename portion survives the sanitiser.
    messages = [record.getMessage() for record in caplog.records]
    combined = " ".join(messages)
    assert "Größe_Test_Wien" in combined, (
        f"Benign umlaut filename was stripped: {combined!r}"
    )


def test_read_capped_text_default_cap_path_log_preserves_filename(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Regression for ``read_capped_text``: same as above."""
    benign = tmp_path / "größe_test_wien.csv"
    benign.write_bytes(b"x" * (DEFAULT_MAX_TEXT_FILE_BYTES + 1))

    caplog.set_level(logging.WARNING)
    result = read_capped_text(benign, max_bytes=100, label="text")
    assert result is None

    messages = [record.getMessage() for record in caplog.records]
    combined = " ".join(messages)
    assert "größe_test_wien" in combined, (
        f"Benign umlaut filename was stripped: {combined!r}"
    )
