"""Sentinel PoC: JSON / text size-bomb defence — Round 6.

Threat model
------------
Rounds 1–5 of the size-bomb family canonicalised the
``Path.open("rb") -> os.fstat(handle.fileno()) -> handle.read(max_bytes+1)``
pattern across **30 covered parsers** (27 disk + 3 network) in 8 modules.
Round 5's inventory walker pinned the canonical eight modules
(``src/utils/files.py``, ``src/utils/stations.py``, ``src/utils/cache.py``,
``src/utils/stations_validation.py``, ``src/places/quota.py``,
``src/places/tiling.py``, ``src/places/merge.py``,
``src/build_feed.py``) — but **explicitly excluded ``src/providers/vor.py``
and ``src/feed/logging.py`` from the canon**.

That exclusion was structural, not intentional: those two modules use a
**different** unsafe shape (``Path.read_text()`` followed by
``json.loads`` / ``str.splitlines``) that doesn't match the
``stat-then-open`` walker pattern at all — they have NO size cap
whatsoever. Worse than the prior canonical sites pre-Round-5: those at
least gated on ``stat().st_size`` (TOCTOU-bypassable but bounded). The
five sites in this round had nothing.

Sites covered
-------------
  * ``src/providers/vor.py:_load_station_name_map`` (line 413 pre-fix) —
    **CRITICAL**: import-time blast radius. The call site
    ``STATION_NAME_MAP = _load_station_name_map()`` runs unconditionally
    on ``import src.providers.vor``, so a planted huge JSON file at
    ``data/vor-haltestellen.mapping.json`` raises ``MemoryError`` at
    module-import time and crashes EVERY consumer (build_feed
    orchestrator, CLI, every script that imports the VOR provider).
  * ``src/providers/vor.py:load_request_count`` (line 1399 pre-fix) —
    HIGH: called per-request from the VOR fetch pipeline; an unbounded
    read here crashes the entire daily quota debit chain.
  * ``src/providers/vor.py:save_request_count`` inner branch (line 1487
    pre-fix) — HIGH: called per-request mid-quota-debit; double-counts
    requests on the next cron run after a crash.
  * ``src/providers/vor.py:_load_station_ids_from_file`` (line 458
    pre-fix) — MEDIUM: CSV path read unbounded; ``Path.read_text`` ->
    ``MemoryError`` propagation.
  * ``src/providers/vor.py:_load_station_ids_default`` (line 480
    pre-fix) — MEDIUM: default catalogue CSV read unbounded.
  * ``src/feed/logging.py:prune_log_file`` (line 139 pre-fix) — MEDIUM:
    log-pruning utility reads the active log file unbounded; a planted
    huge file at the log path raises ``MemoryError`` past the
    surrounding ``except OSError`` and crashes the pruning cron.

Fix shape
---------
The canonical helper ``read_capped_json`` (already TOCTOU-safe via
``os.fstat`` post-Round-5) is reused for the 3 JSON sites. A new
``read_capped_text`` helper mirrors the same TOCTOU + special-file
defence for non-JSON payloads (CSV, log files, .env files) — used by
the 2 CSV sites and the 1 log-prune site.

Both helpers share the TOCTOU-safe shape: ``Path.open("rb")`` ->
``os.fstat(handle.fileno())`` for the size check (immune to symlink
swap mid-resolution) -> ``handle.read(max_bytes + 1)`` defensive read
budget (catches special files like ``/dev/zero`` whose ``st_size == 0``
but yield unbounded bytes).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from src.feed.config import LOG_TIMEZONE


# ============================================================================
# Helpers
# ============================================================================


def _write_oversized_json(path: Path, size_bytes: int) -> None:
    """Write a flat-but-huge JSON list that exceeds the loader's byte cap."""
    payload = "[" + ",".join("0" for _ in range(size_bytes // 2)) + "]"
    path.write_text(payload, encoding="utf-8")


def _write_oversized_text(path: Path, size_bytes: int) -> None:
    """Write a long flat text payload that exceeds the loader's byte cap."""
    path.write_text("a" * size_bytes, encoding="utf-8")


class _LyingPath:
    """Path-like wrapper that returns a fake stat() result.

    Pre-fix, the loader checks ``path.stat().st_size`` — which lies and
    reports a small size — then opens the (real, oversized) file and
    propagates ``MemoryError`` past the catch tuple. Post-fix, the
    loader opens first and calls ``os.fstat`` on the open file
    descriptor, which reports the *real* size (the open call resolved
    the inode atomically). Re-used from
    ``test_sentinel_json_size_bomb_toctou.py`` to confirm the new sites
    inherit the same TOCTOU-safe shape.
    """

    def __init__(self, real_path: Path, fake_size: int) -> None:
        self._real = real_path
        self._fake_size = fake_size

    def stat(self) -> os.stat_result:
        real = self._real.stat()
        return os.stat_result((
            real.st_mode, real.st_ino, real.st_dev, real.st_nlink,
            real.st_uid, real.st_gid,
            self._fake_size,
            real.st_atime, real.st_mtime, real.st_ctime,
        ))

    def open(self, *args: Any, **kwargs: Any) -> Any:
        return self._real.open(*args, **kwargs)

    def exists(self) -> bool:
        return self._real.exists()

    def __fspath__(self) -> str:
        return str(self._real)

    def __str__(self) -> str:
        return str(self._real)


# ============================================================================
# Precondition: the per-loader cap constants exist
# ============================================================================


def test_precondition_vor_size_cap_constants_exist() -> None:
    """Pin the canonical cap constants. If a future refactor renames or
    removes them, every regression test below would silently pass on
    unfixed code — so we pin the precondition first."""
    from src.providers import vor
    from src.feed import logging as feed_logging

    assert isinstance(vor.MAX_VOR_MAPPING_FILE_BYTES, int)
    assert vor.MAX_VOR_MAPPING_FILE_BYTES > 0
    assert vor.MAX_VOR_MAPPING_FILE_BYTES >= 100_000  # > legitimate ~35 KiB

    assert isinstance(vor.MAX_VOR_QUOTA_FILE_BYTES, int)
    assert vor.MAX_VOR_QUOTA_FILE_BYTES > 0

    assert isinstance(vor.MAX_VOR_STATIONS_CSV_FILE_BYTES, int)
    assert vor.MAX_VOR_STATIONS_CSV_FILE_BYTES > 0
    assert vor.MAX_VOR_STATIONS_CSV_FILE_BYTES >= 100_000  # > legitimate ~8 KiB

    assert isinstance(feed_logging.MAX_LOG_PRUNE_FILE_BYTES, int)
    assert feed_logging.MAX_LOG_PRUNE_FILE_BYTES > 0


def test_precondition_read_capped_text_helper_exists() -> None:
    """Pin the new canonical helper. The five new sites depend on it
    being importable with the same signature as ``read_capped_json``."""
    from src.utils.files import read_capped_text, DEFAULT_MAX_TEXT_FILE_BYTES

    assert callable(read_capped_text)
    assert isinstance(DEFAULT_MAX_TEXT_FILE_BYTES, int)
    assert DEFAULT_MAX_TEXT_FILE_BYTES > 0


# ============================================================================
# read_capped_text (the new canonical helper)
# ============================================================================


def test_read_capped_text_rejects_oversized_file(tmp_path: Path) -> None:
    """Pre-fix shape: ``Path.read_text()`` returns the entire file, which
    blows up to ``MemoryError`` for huge files. Post-fix shape: the cap
    fires before ``read()`` runs unbounded, returning ``None``."""
    from src.utils.files import read_capped_text

    target = tmp_path / "data.txt"
    _write_oversized_text(target, 4096)

    result = read_capped_text(target, max_bytes=1024, label="Test")
    assert result is None


def test_read_capped_text_resists_toctou_lying_stat(tmp_path: Path) -> None:
    """PoC: stat-time lie -> open-time large file -> must reject.

    Same TOCTOU shape as ``read_capped_json``: pre-fix code consulting
    ``Path.stat()`` would be fooled, post-fix code calls ``os.fstat`` on
    the open fd and sees the real size.
    """
    from src.utils.files import read_capped_text

    target = tmp_path / "data.txt"
    _write_oversized_text(target, 4096)

    lying = _LyingPath(target, fake_size=10)

    result = read_capped_text(lying, max_bytes=1024, label="Test")  # type: ignore[arg-type]
    assert result is None


def test_read_capped_text_resists_zero_size_special_file(tmp_path: Path) -> None:
    """PoC: special file (``/dev/zero``-like) with ``st_size == 0`` but
    unbounded bytes-on-read. The defense-in-depth ``read(max_bytes + 1)``
    cap is the only protection."""
    from src.utils.files import read_capped_text

    target = tmp_path / "special.txt"
    target.write_text("a" * 50_000, encoding="utf-8")

    real_fstat = os.fstat

    def fake_fstat(fd: int) -> os.stat_result:
        real = real_fstat(fd)
        return os.stat_result((
            real.st_mode, real.st_ino, real.st_dev, real.st_nlink,
            real.st_uid, real.st_gid,
            0,  # lie: zero size
            real.st_atime, real.st_mtime, real.st_ctime,
        ))

    original_fstat = os.fstat
    os.fstat = fake_fstat
    try:
        result = read_capped_text(target, max_bytes=1024, label="Test")
    finally:
        os.fstat = original_fstat

    assert result is None


def test_read_capped_text_accepts_normal_file(tmp_path: Path) -> None:
    """Regression: normal files below the cap parse correctly."""
    from src.utils.files import read_capped_text

    target = tmp_path / "data.txt"
    target.write_text("hello world\nline two\n", encoding="utf-8")

    result = read_capped_text(target, max_bytes=1024, label="Test")
    assert result == "hello world\nline two\n"


def test_read_capped_text_returns_none_for_missing_file(tmp_path: Path) -> None:
    """Regression: missing files yield ``None`` (matches read_capped_json)."""
    from src.utils.files import read_capped_text

    target = tmp_path / "nonexistent.txt"
    result = read_capped_text(target, max_bytes=1024, label="Test")
    assert result is None


def test_read_capped_text_returns_none_for_invalid_utf8(tmp_path: Path) -> None:
    """Regression: invalid UTF-8 yields ``None``, not a propagated
    ``UnicodeDecodeError``."""
    from src.utils.files import read_capped_text

    target = tmp_path / "binary.txt"
    target.write_bytes(b"\xff\xfe\xff\xfe")  # Invalid UTF-8

    result = read_capped_text(target, max_bytes=1024, label="Test")
    assert result is None


# ============================================================================
# src/providers/vor.py — _load_station_name_map (CRITICAL, import-time)
# ============================================================================


def test_vor_load_station_name_map_rejects_oversized_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-fix: ``_load_station_name_map`` called
    ``json.loads(MAPPING_FILE.read_text(encoding=\"utf-8\"))`` with no
    size cap. A planted huge file at ``data/vor-haltestellen.mapping.json``
    buffers O(file_size) bytes, raises ``MemoryError`` at IMPORT TIME
    (the call site ``STATION_NAME_MAP = _load_station_name_map()``
    runs unconditionally on ``import src.providers.vor``), and crashes
    the entire feed-build pipeline.

    Post-fix: ``read_capped_json`` returns ``None`` for the oversized
    file and the function returns the canonical empty mapping.
    """
    from src.providers import vor

    poisoned = tmp_path / "vor-haltestellen.mapping.json"
    _write_oversized_json(poisoned, 4096)

    monkeypatch.setattr(vor, "MAPPING_FILE", poisoned)
    monkeypatch.setattr(vor, "MAX_VOR_MAPPING_FILE_BYTES", 1024)

    # Patch json.loads to confirm it was never reached: post-fix the
    # size cap fires BEFORE the parser runs.
    with patch("src.utils.files.json.loads") as mock_loads:
        result = vor._load_station_name_map()

    mock_loads.assert_not_called()
    assert result == {}


def test_vor_load_station_name_map_accepts_normal_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: normal-sized mapping files still load correctly."""
    from src.providers import vor

    legit = tmp_path / "vor-haltestellen.mapping.json"
    legit.write_text(
        json.dumps([
            {"station_name": "Wien", "resolved_name": "Wien Hbf"},
            {"station_name": "Graz", "resolved_name": "Graz Hbf"},
        ]),
        encoding="utf-8",
    )

    monkeypatch.setattr(vor, "MAPPING_FILE", legit)

    result = vor._load_station_name_map()
    assert result == {"Wien": "Wien Hbf", "Graz": "Graz Hbf"}


# ============================================================================
# src/providers/vor.py — load_request_count (HIGH, per-request)
# ============================================================================


def test_vor_load_request_count_rejects_oversized_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-fix: ``load_request_count`` called
    ``json.loads(REQUEST_COUNT_FILE.read_text(encoding=\"utf-8\"))``
    with no size cap. ``MemoryError`` at this site crashes every VOR
    fetch in the pipeline (``load_request_count`` is invoked per-
    request).

    Post-fix: the size cap returns ``None`` and the canonical fallback
    ``(None, 0)`` is returned, mirroring the file-not-found / corrupt-
    file shape.
    """
    from src.providers import vor

    poisoned = tmp_path / "vor_request_count.json"
    _write_oversized_json(poisoned, 4096)

    monkeypatch.setattr(vor, "REQUEST_COUNT_FILE", poisoned)
    monkeypatch.setattr(vor, "MAX_VOR_QUOTA_FILE_BYTES", 1024)

    with patch("src.utils.files.json.loads") as mock_loads:
        result = vor.load_request_count(bypass_cache=True)

    mock_loads.assert_not_called()
    assert result == (None, 0)


# ============================================================================
# src/providers/vor.py — save_request_count inner branch (HIGH, per-request)
# ============================================================================


def test_vor_save_request_count_rejects_oversized_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-fix: ``save_request_count``'s inner read-back-under-lock
    called ``json.loads(REQUEST_COUNT_FILE.read_text(encoding=\"utf-8\"))``
    with no size cap. A poisoned counter file would propagate
    ``MemoryError`` mid-quota-debit, potentially double-counting
    requests on the next run.

    Post-fix: the oversized file is treated as unreadable and the
    in-memory delta is committed, restoring the canonical schema for
    the next reader.
    """
    from src.providers import vor

    poisoned = tmp_path / "vor_request_count.json"
    _write_oversized_json(poisoned, 4096)

    monkeypatch.setattr(vor, "REQUEST_COUNT_FILE", poisoned)
    monkeypatch.setattr(vor, "MAX_VOR_QUOTA_FILE_BYTES", 1024)
    monkeypatch.setenv("WIEN_OEPNV_TEST_QUOTA_BATCH", "1")

    # Reset cache so the call goes through the I/O path.
    vor._QUOTA_CACHE["date"] = ""
    vor._QUOTA_CACHE["count"] = 0
    vor._QUOTA_CACHE["unsaved_delta"] = 0
    try:
        result = vor.save_request_count()
    finally:
        vor._QUOTA_CACHE["date"] = ""
        vor._QUOTA_CACHE["count"] = 0
        vor._QUOTA_CACHE["unsaved_delta"] = 0

    # Post-fix: the in-memory increment is committed; the legacy/poisoned
    # file is overwritten with the canonical schema.
    assert isinstance(result, int)
    assert result >= 1


# ============================================================================
# src/providers/vor.py — _load_station_ids_from_file (MEDIUM)
# ============================================================================


def test_vor_load_station_ids_from_file_rejects_oversized_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-fix: ``_load_station_ids_from_file`` called
    ``path.read_text(encoding=\"utf-8\")`` with no size cap. Post-fix:
    ``read_capped_text`` returns ``None`` for oversized files and the
    canonical empty list is returned."""
    from src.providers import vor

    poisoned = tmp_path / "stations.csv"
    _write_oversized_text(poisoned, 4096)

    monkeypatch.setattr(vor, "MAX_VOR_STATIONS_CSV_FILE_BYTES", 1024)

    result = vor._load_station_ids_from_file(poisoned)
    assert result == []


def test_vor_load_station_ids_default_rejects_oversized_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-fix: ``_load_station_ids_default`` called
    ``DEFAULT_STATION_ID_FILE.read_text(encoding=\"utf-8\").splitlines()``
    with no size cap. Post-fix: the cap rejects oversized files and the
    canonical empty list is returned."""
    from src.providers import vor

    poisoned = tmp_path / "vor-haltestellen.csv"
    _write_oversized_text(poisoned, 4096)

    monkeypatch.setattr(vor, "DEFAULT_STATION_ID_FILE", poisoned)
    monkeypatch.setattr(vor, "MAX_VOR_STATIONS_CSV_FILE_BYTES", 1024)

    # ``vor_station_ids()`` may yield from the canonical store; force the
    # fallback branch by patching it to return an empty iterator.
    monkeypatch.setattr(vor, "vor_station_ids", lambda: iter(()))

    result = vor._load_station_ids_default()
    assert result == []


# ============================================================================
# src/feed/logging.py — prune_log_file (MEDIUM)
# ============================================================================


def test_prune_log_file_rejects_oversized_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pre-fix: ``prune_log_file`` called ``path.read_text(encoding=\"utf-8\")``
    with no size cap; a planted huge file would propagate ``MemoryError``
    out of ``prune_log_file`` and crash the cron job.

    Post-fix: the size cap returns ``None`` and ``prune_log_file``
    returns silently, matching the prior ``except OSError`` shape.
    """
    from src.feed import logging as feed_logging

    log_path = tmp_path / "errors.log"
    _write_oversized_text(log_path, 4096)

    monkeypatch.setattr(feed_logging, "MAX_LOG_PRUNE_FILE_BYTES", 1024)

    caplog.set_level(logging.WARNING)

    # The function must NOT raise — pre-fix it would propagate
    # ``MemoryError`` out of ``read_text`` in extreme cases. Even more
    # importantly, it must NOT rewrite the file (which would be
    # another way the cron would clobber an oversized log).
    snapshot = log_path.read_bytes()
    feed_logging.prune_log_file(log_path, now=datetime(2026, 5, 8, tzinfo=LOG_TIMEZONE))
    assert log_path.read_bytes() == snapshot, (
        "prune_log_file must not modify the file when its size exceeds "
        "MAX_LOG_PRUNE_FILE_BYTES — the cap fires before the parser runs."
    )


def test_prune_log_file_normal_file_unaffected(
    tmp_path: Path,
) -> None:
    """Regression: normal log files are pruned correctly."""
    from src.feed import logging as feed_logging

    log_path = tmp_path / "errors.log"
    log_path.write_text(
        "2025-01-01 00:00:00,000 INFO test: very old line\n"
        "2026-05-08 00:00:00,000 INFO test: recent line\n",
        encoding="utf-8",
    )

    feed_logging.prune_log_file(
        log_path,
        now=datetime(2026, 5, 8, 12, 0, 0, tzinfo=LOG_TIMEZONE),
        keep_days=7,
    )

    remaining = log_path.read_text(encoding="utf-8")
    assert "very old line" not in remaining
    assert "recent line" in remaining
