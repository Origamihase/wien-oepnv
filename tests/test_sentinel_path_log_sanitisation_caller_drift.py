"""Sentinel PoC: Sibling drift of the 2026-05-11
``read_capped_json``/``read_capped_text`` path-log sanitisation fix.

The canonical fix (PR #1456) routed the ``path`` argument through a
truncated SHA-256 fingerprint at the WARNING log boundary so a hostile
path string (Trojan-Source BiDi, zero-width, 8-bit C1, Tag block,
Variation Selectors, log-injection newline / CR / BEL, ANSI escape
prefix) cannot leak verbatim into operator-facing logs and the public
``docs/feed_health.json`` artefact. The fix was applied at the canonical
helper boundary in ``src/utils/files.py`` but FIVE caller-side sibling
log sites in three modules continued to interpolate the path bytes
through the bare ``%s`` format spec:

  1. ``src/utils/stations.py:_read_capped_json`` — a LITERAL duplicate
     of the canonical helper that was missed when PR #1456 fixed the
     ``src/utils/files.py`` original. Two WARNING log lines (size-cap
     and read-cap branches) interpolate ``path`` verbatim. Reached via
     the two ``@lru_cache`` import-time loaders ``_station_entries`` /
     ``_vienna_polygons`` so a successful primitive-injection lands in
     EVERY feed-build path that touches a station name or a Vienna
     geo-fence check.

  2. ``src/utils/env.py:_warn_if_world_readable`` — emits a WARNING
     when an ``.env`` candidate file has group-/world-readable bits,
     interpolating ``path`` TWICE verbatim. The candidate paths are
     env-controlled via ``WIEN_OEPNV_ENV_FILES`` and resolved relative
     to the repo root; an attacker who can plant a file under any
     allowed sub-tree (or who controls the checkout location)
     poisons the WARNING log.

  3. ``src/utils/env.py:load_env_file`` — emits a WARNING when
     ``read_capped_text`` returns ``None`` (file too large / invalid
     UTF-8 / I/O error), interpolating ``path`` verbatim. Same
     env-controlled candidate-list surface as Site 2.

  4. ``src/build_feed.py:_load_state`` — orchestrator state file
     (``data/first_seen.json`` or ``STATE_PATH``-overridden path).
     The path is ``feed_config.STATE_FILE`` which is operator-
     controlled via the ``STATE_PATH`` environment variable. Two
     WARNING log lines (size-cap and read-cap branches) interpolate
     ``path`` verbatim.

  5. ``src/build_feed.py:_read_state_capped`` — orchestrator state
     file's safe-merge path under exclusive lock. Same path surface
     and same two-WARNING log shape as Site 4.

  6. ``src/build_feed.py:_save_state`` lock-failure branch —
     interpolates ``path`` verbatim into a WARNING when the
     advisory file-lock cannot be acquired. Same path surface as
     Sites 4 / 5.

Threat model
============
A hostile path string can carry the canonical primitives:

  * ``‮`` RIGHT-TO-LEFT OVERRIDE — visually reverses subsequent
    text. An operator skimming the log sees the inverse of the actual
    bytes. Phishing primitive in any artefact the log feeds into
    (``docs/feed_health.json``, GitHub Issue auto-submission).
  * ``​`` ZERO WIDTH SPACE — invisible cache-key / equality
    poisoning primitive.
  * ``\x9b`` 8-bit CSI / ``\x9d`` 8-bit OSC — terminal-escape
    primitives that survive the 7-bit ``_ANSI_ESCAPE_RE`` defence
    and trigger SGR colour interpretation on 8-bit-C1-honouring
    terminals (xterm with eightBitInput, several BSD consoles,
    rxvt in 8-bit mode).
  * ``\x1b`` ESC — ANSI prefix; terminal-escape primitive.
  * ``\x07`` BEL — terminal-bell denial-of-attention.
  * ``\n`` / ``\r`` — log-record forgery in any line-based consumer.
  * ``\U000e0020`` Unicode Tag SPACE — invisible-instruction
    smuggling primitive (2024 OpenAI disclosure).
  * ``︀`` VARIATION SELECTOR-1 — 4-bit-payload steganography.

Pre-fix every primitive flows verbatim into the WARNING log line and
from there into the public ``docs/feed_health.json`` artefact + the
GitHub Issue body submitted by ``submit_auto_issue``. Post-fix the
SHA-256 fingerprint (hex-only, ``[0-9a-f]{12}``) replaces the path
string at the interpolation boundary so no primitive can survive.

Defence shape (mirrors PR #1456)
================================
``sha256(str(path).encode("utf-8", errors="replace")).hexdigest()[:12]``

This is:
  * A CodeQL-recognised barrier (``hashlib`` is a documented sanitiser
    sink — secret-bearing taint cannot survive a cryptographic hash).
  * Trojan-Source-clean — the hex representation is ``[0-9a-f]`` only.
  * Operator-correlatable — running ``sha256(str(path))[:12]`` locally
    on a candidate path confirms identity.
  * Stable across runs for a given path — useful for log aggregation.
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Any, NoReturn

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


# ============================================================================
# Site 1: src/utils/stations.py:_station_entries  (size-cap WARNING)
# ============================================================================


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_station_entries_size_cap_log_strips_path_primitives(
    primitive: str,
    primitive_label: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A poisoned ``_STATIONS_PATH`` that triggers the size-cap WARNING
    MUST NOT propagate the primitive into the log output.

    Pre-fix ``stations._read_capped_json`` interpolated ``path`` via the
    bare ``%s`` format spec; post-fix the SHA-256 fingerprint replaces
    the path string at the boundary.
    """
    from src.utils import stations

    poisoned_dir = tmp_path / f"dir{primitive}"
    poisoned_dir.mkdir()
    poisoned = poisoned_dir / "stations.json"
    poisoned.write_bytes(b"x" * 4096)

    monkeypatch.setattr(stations, "_STATIONS_PATH", poisoned, raising=False)
    monkeypatch.setattr(
        stations, "MAX_STATIONS_FILE_BYTES", 1024, raising=False
    )
    stations._station_entries.cache_clear()

    caplog.set_level(logging.WARNING, logger="src.utils.stations")
    caplog.set_level(logging.WARNING, logger="src.utils.files")
    result = stations._station_entries()
    stations._station_entries.cache_clear()

    assert result == ()
    for record in caplog.records:
        message = record.getMessage()
        assert primitive not in message, (
            f"{primitive_label} ({primitive!r}) leaked through "
            f"_station_entries size-cap WARNING: {message!r}"
        )


def test_station_entries_size_cap_log_carries_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Operator-correlation invariant: the size-cap WARNING carries the
    truncated SHA-256 of the path bytes."""
    from src.utils import stations

    benign = tmp_path / "stations.json"
    benign.write_bytes(b"x" * 4096)

    monkeypatch.setattr(stations, "_STATIONS_PATH", benign, raising=False)
    monkeypatch.setattr(
        stations, "MAX_STATIONS_FILE_BYTES", 1024, raising=False
    )
    stations._station_entries.cache_clear()

    caplog.set_level(logging.WARNING)
    result = stations._station_entries()
    stations._station_entries.cache_clear()

    assert result == ()
    combined = " ".join(record.getMessage() for record in caplog.records)
    assert _fingerprint(benign) in combined, (
        f"Fingerprint {_fingerprint(benign)!r} missing from log: {combined!r}"
    )


# ============================================================================
# Site 2: src/utils/stations.py:_vienna_polygons  (size-cap WARNING)
# ============================================================================


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_vienna_polygons_size_cap_log_strips_path_primitives(
    primitive: str,
    primitive_label: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Sibling invariant: ``_vienna_polygons`` shares the same helper
    and therefore the same fix shape."""
    from src.utils import stations

    poisoned_dir = tmp_path / f"polydir{primitive}"
    poisoned_dir.mkdir()
    poisoned = poisoned_dir / "polygon.json"
    poisoned.write_bytes(b"x" * 4096)

    monkeypatch.setattr(
        stations, "_VIENNA_POLYGON_PATH", poisoned, raising=False
    )
    monkeypatch.setattr(
        stations, "MAX_VIENNA_POLYGON_FILE_BYTES", 1024, raising=False
    )
    stations._vienna_polygons.cache_clear()

    caplog.set_level(logging.WARNING, logger="src.utils.stations")
    caplog.set_level(logging.WARNING, logger="src.utils.files")
    result = stations._vienna_polygons()
    stations._vienna_polygons.cache_clear()

    assert result == ()
    for record in caplog.records:
        message = record.getMessage()
        assert primitive not in message, (
            f"{primitive_label} ({primitive!r}) leaked through "
            f"_vienna_polygons size-cap WARNING: {message!r}"
        )


# ============================================================================
# Site 3: src/utils/env.py:_warn_if_world_readable  (group/world-readable WARNING)
# ============================================================================


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_warn_if_world_readable_strips_path_primitives(
    primitive: str,
    primitive_label: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``_warn_if_world_readable`` logs the path twice via the bare
    ``%s`` format spec when a group-/world-readable env file is
    detected. The path string can carry Trojan-Source primitives.

    Pre-fix the primitives flow into the WARNING log verbatim; post-fix
    the SHA-256 fingerprint replaces the path string at both
    interpolation points.
    """
    if os.name != "posix":
        pytest.skip("POSIX-only: world-readable check is gated on os.name")

    from src.utils import env as env_module

    poisoned_dir = tmp_path / f"envdir{primitive}"
    poisoned_dir.mkdir()
    poisoned = poisoned_dir / ".env"
    poisoned.write_text("FOO=bar\n", encoding="utf-8")
    poisoned.chmod(0o644)  # group/world readable to trigger the WARNING

    caplog.set_level(logging.WARNING)
    env_module._warn_if_world_readable(poisoned)

    for record in caplog.records:
        message = record.getMessage()
        assert primitive not in message, (
            f"{primitive_label} ({primitive!r}) leaked through "
            f"_warn_if_world_readable WARNING: {message!r}"
        )


def test_warn_if_world_readable_log_carries_fingerprint(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Operator-correlation invariant."""
    if os.name != "posix":
        pytest.skip("POSIX-only")

    from src.utils import env as env_module

    benign = tmp_path / "benign.env"
    benign.write_text("FOO=bar\n", encoding="utf-8")
    benign.chmod(0o644)

    caplog.set_level(logging.WARNING)
    env_module._warn_if_world_readable(benign)

    combined = " ".join(record.getMessage() for record in caplog.records)
    assert _fingerprint(benign) in combined, (
        f"Fingerprint {_fingerprint(benign)!r} missing from log: {combined!r}"
    )


# ============================================================================
# Site 4: src/utils/env.py:load_env_file  (read_capped_text fallback WARNING)
# ============================================================================


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_load_env_file_oversize_log_strips_path_primitives(
    primitive: str,
    primitive_label: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A poisoned env-file path that exceeds ``MAX_ENV_FILE_BYTES``
    triggers the caller-side WARNING in ``load_env_file`` (the
    ``read_capped_text`` helper logs its own sanitised line and
    returns ``None``; the caller logs an ADDITIONAL line that pre-fix
    interpolated ``path`` verbatim).
    """
    from src.utils import env as env_module

    poisoned_dir = tmp_path / f"envdir{primitive}"
    poisoned_dir.mkdir()
    poisoned = poisoned_dir / ".env"
    poisoned.write_bytes(b"x" * 4096)
    if os.name == "posix":
        poisoned.chmod(0o600)

    monkeypatch.setattr(
        env_module, "MAX_ENV_FILE_BYTES", 1024, raising=False
    )

    caplog.set_level(logging.WARNING)
    result = env_module.load_env_file(poisoned, environ={})
    assert result == {}

    for record in caplog.records:
        message = record.getMessage()
        assert primitive not in message, (
            f"{primitive_label} ({primitive!r}) leaked through "
            f"load_env_file caller-side WARNING: {message!r}"
        )


def test_load_env_file_oversize_log_carries_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Operator-correlation invariant for the ``load_env_file`` caller-
    side WARNING."""
    from src.utils import env as env_module

    benign = tmp_path / ".env"
    benign.write_bytes(b"x" * 4096)
    if os.name == "posix":
        benign.chmod(0o600)

    monkeypatch.setattr(
        env_module, "MAX_ENV_FILE_BYTES", 1024, raising=False
    )

    caplog.set_level(logging.WARNING)
    env_module.load_env_file(benign, environ={})

    combined = " ".join(record.getMessage() for record in caplog.records)
    assert _fingerprint(benign) in combined, (
        f"Fingerprint {_fingerprint(benign)!r} missing from log: {combined!r}"
    )


# ============================================================================
# Site 5: src/build_feed.py:_load_state  (state-file size-cap WARNING)
# ============================================================================


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_load_state_size_cap_log_strips_path_primitives(
    primitive: str,
    primitive_label: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``_load_state`` interpolates the ``STATE_FILE`` path via the bare
    ``%s`` format spec. The state-file path is operator-controlled via
    the ``STATE_PATH`` environment variable; a hostile path string
    carrying Trojan-Source primitives flows into the WARNING log.
    """
    import src.build_feed as build_feed_mod

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    state_dir = tmp_path / "data" / f"dir{primitive}"
    state_dir.mkdir()
    state_file = state_dir / "state.json"
    state_file.write_bytes(b"x" * 4096)

    monkeypatch.setattr(
        build_feed_mod, "validate_path",
        lambda *args, **kwargs: state_file,
        raising=False,
    )
    monkeypatch.setattr(
        build_feed_mod, "MAX_STATE_FILE_BYTES", 1024, raising=False
    )

    caplog.set_level(logging.WARNING)
    result = build_feed_mod._load_state()

    assert result == {}
    for record in caplog.records:
        message = record.getMessage()
        assert primitive not in message, (
            f"{primitive_label} ({primitive!r}) leaked through "
            f"_load_state size-cap WARNING: {message!r}"
        )


def test_load_state_size_cap_log_carries_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Operator-correlation invariant for the ``_load_state`` size-cap
    WARNING."""
    import src.build_feed as build_feed_mod

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    state_file = tmp_path / "data" / "state.json"
    state_file.write_bytes(b"x" * 4096)

    monkeypatch.setattr(
        build_feed_mod, "validate_path",
        lambda *args, **kwargs: state_file,
        raising=False,
    )
    monkeypatch.setattr(
        build_feed_mod, "MAX_STATE_FILE_BYTES", 1024, raising=False
    )

    caplog.set_level(logging.WARNING)
    build_feed_mod._load_state()

    combined = " ".join(record.getMessage() for record in caplog.records)
    assert _fingerprint(state_file) in combined, (
        f"Fingerprint {_fingerprint(state_file)!r} missing from log: "
        f"{combined!r}"
    )


# ============================================================================
# Site 6: src/build_feed.py:_read_state_capped (safe-merge WARNING)
# ============================================================================


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_read_state_capped_size_cap_log_strips_path_primitives(
    primitive: str,
    primitive_label: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``_read_state_capped`` is the safe-merge sibling of
    ``_load_state``; it has its own WARNING log line interpolating
    ``path`` via the bare ``%s`` format spec."""
    import src.build_feed as build_feed_mod

    state_dir = tmp_path / f"dir{primitive}"
    state_dir.mkdir()
    state_file = state_dir / "state.json"
    state_file.write_bytes(b"x" * 4096)

    monkeypatch.setattr(
        build_feed_mod, "MAX_STATE_FILE_BYTES", 1024, raising=False
    )

    caplog.set_level(logging.WARNING)
    result = build_feed_mod._read_state_capped(state_file)

    assert result == {}
    for record in caplog.records:
        message = record.getMessage()
        assert primitive not in message, (
            f"{primitive_label} ({primitive!r}) leaked through "
            f"_read_state_capped size-cap WARNING: {message!r}"
        )


def test_read_state_capped_log_carries_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Operator-correlation invariant for ``_read_state_capped``."""
    import src.build_feed as build_feed_mod

    state_file = tmp_path / "state.json"
    state_file.write_bytes(b"x" * 4096)

    monkeypatch.setattr(
        build_feed_mod, "MAX_STATE_FILE_BYTES", 1024, raising=False
    )

    caplog.set_level(logging.WARNING)
    build_feed_mod._read_state_capped(state_file)

    combined = " ".join(record.getMessage() for record in caplog.records)
    assert _fingerprint(state_file) in combined, (
        f"Fingerprint {_fingerprint(state_file)!r} missing from log: "
        f"{combined!r}"
    )


# ============================================================================
# Site 7: src/build_feed.py:_save_state lock-failure branch
# ============================================================================


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_save_state_lock_fail_log_strips_path_primitives(
    primitive: str,
    primitive_label: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The ``_save_state`` lock-failure branch interpolates ``path``
    verbatim into a WARNING when the advisory file-lock cannot be
    acquired. The path string can carry Trojan-Source primitives.
    """
    import src.build_feed as build_feed_mod
    from src.utils import locking as locking_module

    state_dir = tmp_path / f"dir{primitive}"
    state_dir.mkdir()
    state_file = state_dir / "state.json"

    monkeypatch.setattr(
        build_feed_mod, "validate_path",
        lambda *args, **kwargs: state_file,
        raising=False,
    )

    # Force the lock-acquisition path into its OSError branch by patching
    # ``file_lock`` (the helper that raises on contention). This is more
    # targeted than patching ``Path.open`` globally — only the lock
    # acquisition fails, not the surrounding I/O.
    def _raise_oserror(*args: Any, **kwargs: Any) -> NoReturn:
        raise OSError("simulated lock acquisition failure")

    monkeypatch.setattr(
        build_feed_mod, "file_lock", _raise_oserror, raising=False,
    )
    monkeypatch.setattr(
        locking_module, "file_lock", _raise_oserror, raising=False,
    )

    caplog.set_level(logging.WARNING)
    build_feed_mod._save_state({"k": {"first_seen": "2026-05-11T00:00:00+00:00"}})

    for record in caplog.records:
        message = record.getMessage()
        assert primitive not in message, (
            f"{primitive_label} ({primitive!r}) leaked through "
            f"_save_state lock-fail WARNING: {message!r}"
        )


# ============================================================================
# Additive-regression: legitimate German content survives
# ============================================================================


def test_legitimate_german_path_survives_fingerprint(tmp_path: Path) -> None:
    """The fingerprint shape must accept legitimate German filenames
    (umlauts, sharp-s) without rejection. SHA-256 is byte-clean for
    any UTF-8 input, so this is a smoke test."""
    legit = tmp_path / "Größe_Test_Wien.json"
    legit.write_bytes(b"x" * 1024)
    fp = _fingerprint(legit)
    assert len(fp) == 12
    assert all(ch in "0123456789abcdef" for ch in fp)


def test_fingerprint_stable_across_runs(tmp_path: Path) -> None:
    """The fingerprint is deterministic; running it twice on the same
    path yields the same digest. Operators correlate by re-hashing
    candidate paths locally."""
    path = tmp_path / "stable.json"
    a = _fingerprint(path)
    b = _fingerprint(path)
    assert a == b
    # Different path → different digest (overwhelmingly likely under
    # SHA-256's collision resistance).
    other = tmp_path / "different.json"
    assert _fingerprint(other) != a
