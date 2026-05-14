"""Sentinel PoC: Round-3 sibling drift of the 2026-05-13 ``scripts/``
path-log sanitisation fix (PR #1468).

The 2026-05-13 Round-2 closure enumerated 28 caller-side WARNING/INFO/ERROR
log sinks across three CLI-driven scripts (``enrich_station_aliases.py``,
``update_station_directory.py``, ``update_wl_stations.py``) that interpolated
operator-controlled ``path`` arguments verbatim via the bare ``%s`` format
spec. The closing-checklist grep for that round was explicitly scoped to the
three scripts named in the journal entry — it did NOT enumerate the cron-
pipeline sibling ``scripts/update_baustellen_cache.py`` which carries the
same drift shape against the env-controlled fallback path and the env-
controlled raw path string.

The ``update_baustellen_cache.py`` script runs every 30-minute cron tick
inside ``.github/workflows/update-cycle.yml``:

    python scripts/update_baustellen_cache.py > "$log_dir/baustellen.log" 2>&1 &

so any Trojan-Source primitive that flows into one of the WARNING / INFO /
ERROR log lines below lands inside ``$log_dir/baustellen.log`` (captured
into the GitHub Actions logs and any downstream SIEM ingest) and into any
``LogRecord`` consumer that reads ``record.args`` before the
:class:`src.feed.logging_safe.SafeFormatter` runs.

Sites this PoC enumerates (all log the operator-controlled ``path`` /
``text`` via the bare ``%s`` format spec):

  * ``_load_fallback``                       L235 (FNF ERROR)
                                             L237 (use-fallback INFO)
                                             L250 (parse-failure ERROR)
                                             L263 (shape-error ERROR)
  * ``_resolve_fallback_path``               L347 (path-traversal WARNING)

Threat model
============
Both ``BAUSTELLEN_FALLBACK_PATH`` (operator env-var, default
``data/samples/baustellen_sample.geojson``) and the raw env string of that
var land in the log lines above. A hostile env / CI runner / leaked secret
store value carrying any of the canonical CVE-2021-42574 / log-injection /
8-bit-C1 / Tag-block / Variation-Selector / ANSI-ESC / log-forgery
primitive bytes flows verbatim into:

  * The aggregated cron log file ``$log_dir/baustellen.log`` (captured by
    the workflow run and visible in the GitHub Actions UI / ingested by
    any SIEM forwarder).
  * Pytest's ``caplog`` capture (which exposes ``record.args[0]`` BEFORE
    the :class:`SafeFormatter` runs — a third-party log handler or
    custom plugin sees the raw bytes).
  * Any downstream consumer that reads ``record.msg`` /
    ``record.getMessage()`` from the propagated record before formatter
    sanitisation (rsyslog with Python logging adapters, structured log
    JSON emitters that don't route through :class:`SafeJSONFormatter`).

Defence shape
=============
Mirrors the canonical ``_path_fingerprint`` shape from Round 2::

    hashlib.sha256(str(path).encode("utf-8", errors="replace")).hexdigest()[:12]

This is:
  * A CodeQL-recognised barrier (``hashlib`` is a documented sanitiser
    sink for ``py/clear-text-logging-sensitive-data``).
  * Trojan-Source-clean — hex-only ``[0-9a-f]`` output.
  * Operator-correlatable — running ``sha256(str(path))[:12]`` locally
    on a candidate path confirms identity for cron-pipeline diagnosis.
  * Stable across runs for a given path — useful for log aggregation
    and SIEM grouping.

The script gains:
  1. A module-level ``import hashlib``.
  2. A module-level ``_path_fingerprint`` helper mirroring
     :func:`src.utils.env._path_fingerprint`.
  3. Every operator-controlled-path WARNING / INFO / ERROR log line is
     updated to use ``[path-sha256=%s]`` with ``_path_fingerprint(path)``
     in place of the raw ``path`` argument.
"""
from __future__ import annotations

import hashlib
import logging
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


def _fingerprint(value: str | Path) -> str:
    """Return the canonical 12-hex SHA-256 fingerprint of *value*."""
    return hashlib.sha256(
        str(value).encode("utf-8", errors="replace")
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
            f"{site_label} message: {message!r}"
        )
        for arg in record.args or ():
            assert primitive not in str(arg), (
                f"{primitive_label} ({primitive!r}) leaked through "
                f"{site_label} log args: {arg!r}"
            )


# ============================================================================
# scripts/update_baustellen_cache.py — _load_fallback (4 sites)
# ============================================================================


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_baustellen_load_fallback_missing_strips_primitive(
    primitive: str,
    primitive_label: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """L235: ``_load_fallback`` ERROR sink when ``path.exists()`` is False."""
    from scripts.update_baustellen_cache import _load_fallback

    path = _poisoned_path(tmp_path, primitive, "baustellen.geojson")
    # Do NOT create the file — exercise the FNF branch.
    caplog.set_level(logging.ERROR)
    result = _load_fallback(path)
    assert result is None
    _assert_primitive_absent(
        caplog, primitive, primitive_label, "baustellen:_load_fallback L235"
    )


def test_baustellen_load_fallback_missing_emits_fingerprint(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """L235 positive: the fingerprint of the FNF path appears in a log line."""
    from scripts.update_baustellen_cache import _load_fallback

    benign = tmp_path / "missing-baustellen.geojson"
    caplog.set_level(logging.ERROR)
    _load_fallback(benign)
    combined = " ".join(r.getMessage() for r in caplog.records)
    assert _fingerprint(benign) in combined, (
        f"fingerprint missing from L235: {combined!r}"
    )


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_baustellen_load_fallback_present_info_strips_primitive(
    primitive: str,
    primitive_label: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """L237: ``_load_fallback`` INFO sink fires once the file exists."""
    from scripts.update_baustellen_cache import _load_fallback

    path = _poisoned_path(tmp_path, primitive, "baustellen.geojson")
    path.write_text(
        '{"type": "FeatureCollection", "features": []}',
        encoding="utf-8",
    )
    caplog.set_level(logging.INFO)
    result = _load_fallback(path)
    assert result == {"type": "FeatureCollection", "features": []}
    _assert_primitive_absent(
        caplog, primitive, primitive_label, "baustellen:_load_fallback L237"
    )


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_baustellen_load_fallback_invalid_json_strips_primitive(
    primitive: str,
    primitive_label: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """L250: ``_load_fallback`` ERROR sink when ``read_capped_json`` returns None."""
    from scripts.update_baustellen_cache import _load_fallback

    path = _poisoned_path(tmp_path, primitive, "baustellen.geojson")
    path.write_bytes(b"not json{{{")  # corrupted
    caplog.set_level(logging.ERROR)
    result = _load_fallback(path)
    assert result is None
    _assert_primitive_absent(
        caplog, primitive, primitive_label, "baustellen:_load_fallback L250"
    )


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_baustellen_load_fallback_wrong_shape_strips_primitive(
    primitive: str,
    primitive_label: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """L263: ``_load_fallback`` ERROR sink when payload is not a JSON object."""
    from scripts.update_baustellen_cache import _load_fallback

    path = _poisoned_path(tmp_path, primitive, "baustellen.geojson")
    # JSON array — decodes successfully but ``isinstance(payload, dict)`` is False.
    path.write_text("[1, 2, 3]", encoding="utf-8")
    caplog.set_level(logging.ERROR)
    result = _load_fallback(path)
    assert result is None
    _assert_primitive_absent(
        caplog, primitive, primitive_label, "baustellen:_load_fallback L263"
    )


# ============================================================================
# scripts/update_baustellen_cache.py — _resolve_fallback_path (1 site)
# ============================================================================


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_baustellen_resolve_fallback_path_traversal_strips_primitive(
    primitive: str,
    primitive_label: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """L347: ``_resolve_fallback_path`` WARNING on path-traversal — the raw
    env-controlled ``text`` (path string) is interpolated verbatim via ``%s``.
    """
    from scripts.update_baustellen_cache import (
        DEFAULT_FALLBACK_PATH,
        _resolve_fallback_path,
    )

    # Construct an absolute path that resolves outside REPO_ROOT and carries
    # the primitive in its byte representation. ``/tmp/<primitive>...`` is a
    # natural attack shape — operator runs the script with
    # ``BAUSTELLEN_FALLBACK_PATH=/tmp/poisoned`` pointing at a sibling file
    # the CI runner can read.
    poisoned_text = f"/tmp/dir{primitive}sub/baustellen.geojson"
    caplog.set_level(logging.WARNING)
    result = _resolve_fallback_path(poisoned_text)
    # The traversal branch falls back to the bundled default.
    assert result == DEFAULT_FALLBACK_PATH
    _assert_primitive_absent(
        caplog,
        primitive,
        primitive_label,
        "baustellen:_resolve_fallback_path L347",
    )


def test_baustellen_resolve_fallback_path_traversal_emits_fingerprint(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """L347 positive: the fingerprint of the offending path appears in the log."""
    from scripts.update_baustellen_cache import _resolve_fallback_path

    poisoned_text = "/tmp/outside-repo/baustellen.geojson"
    caplog.set_level(logging.WARNING)
    _resolve_fallback_path(poisoned_text)
    combined = " ".join(r.getMessage() for r in caplog.records)
    assert _fingerprint(Path(poisoned_text)) in combined, (
        f"fingerprint missing from L347: {combined!r}"
    )


# ============================================================================
# Invariant pin: ``_path_fingerprint`` matches the canonical shape.
# ============================================================================


def test_baustellen_path_fingerprint_matches_canonical_shape() -> None:
    """The module's ``_path_fingerprint`` helper MUST mirror the shape
    pinned in :func:`src.utils.env._path_fingerprint` byte-for-byte so the
    cross-script ``[path-sha256=...]`` token shape stays single-sourced.
    """
    from scripts.update_baustellen_cache import _path_fingerprint as fp_baustellen
    from src.utils.env import _path_fingerprint as fp_canonical

    sample = Path("/tmp/some-path/baustellen.geojson")
    assert fp_baustellen(sample) == fp_canonical(sample)
    # 12 hex characters, no padding, no separator.
    assert len(fp_baustellen(sample)) == 12
    assert all(c in "0123456789abcdef" for c in fp_baustellen(sample))


def test_baustellen_path_fingerprint_handles_non_utf8_bytes() -> None:
    """``errors="replace"`` ensures a path with bytes that aren't valid
    UTF-8 still produces a stable fingerprint instead of raising.
    """
    from scripts.update_baustellen_cache import _path_fingerprint

    # ``str(Path)`` always returns ``str``; this exercises the .encode()
    # ``errors="replace"`` contract on the helper itself by feeding a path
    # that contains lone surrogates (which str.encode would otherwise reject).
    poisoned = Path("/tmp/\udc80-lone-surrogate")
    fp = _path_fingerprint(poisoned)
    assert len(fp) == 12
    assert all(c in "0123456789abcdef" for c in fp)
