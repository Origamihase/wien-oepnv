"""Sentinel PoC: TOCTOU bypass of the canonical size-bomb cap.

Threat model
------------
Rounds 1–4 of the JSON size-bomb defence canonicalised the
"stat-then-cap-then-read" pattern across **27 on-disk JSON parsers** in
13 modules. Every site shares the same shape::

    if path.stat().st_size > MAX_*_FILE_BYTES:
        return None / raise / etc.
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

The byte-size cap fires *before* ``open()`` so the file content is
never buffered into memory when oversized — that was the canonical
defence pinned in Round 1's `513dcb4`. **But** the cap is implemented
across **two distinct syscalls** (``Path.stat`` resolves the path AND
follows symlinks, then ``Path.open`` resolves the path AGAIN and
follows the symlink AGAIN), and an attacker who can swap the inode at
``path`` between those two calls bypasses the cap entirely:

  T0:  ``path`` is a symlink → ``small.json`` (under cap)
  T1:  ``path.stat().st_size`` returns the small target's size → cap passes
  T2:  attacker atomically replaces the symlink to point to ``big.json``
       via ``os.replace(tmp_link, path)``
  T3:  ``path.open("r")`` re-resolves the symlink → opens ``big.json``
       (over cap)
  T4:  ``json.load(handle)`` buffers the whole 1 GiB file →
       ``MemoryError`` propagates past the surrounding
       ``except (OSError, json.JSONDecodeError, RecursionError)`` (a
       ``BaseException``-rooted class is NOT in the catch tuple) and
       crashes the cron pipeline.

This is the same `BaseException`-rooted memory-exhaustion class as the
on-disk and network rounds: the depth-bomb catch tuple does not cover
it, the byte-size cap is the *only* defence — and the cap is
TOCTOU-bypassable as currently implemented.

Threat actor model: identical to the prior rounds — compromised CI
runner / partial flush + power loss / corrupted previous run / a
parallel orchestrator process that atomically swaps the inode under
the loader's feet via ``atomic_write``'s ``os.replace`` step.

Fix shape
---------
Open the file first; fstat the *open file descriptor*. ``os.fstat(fd)``
returns the size of the inode the open() call resolved, regardless of
any subsequent symlink swaps — closing the TOCTOU window. As a
defense-in-depth additional check, read at most ``max_bytes + 1``
bytes and reject the file if ``len(raw) > max_bytes``: this catches
special files (FIFOs, ``/dev/zero``, character devices) where
``st_size`` is 0 but the read returns unbounded bytes.

Sites covered
-------------
This file pins the canonical helper ``src.utils.files.read_capped_json``
(used by 16+ scripts via the shared-helper pattern) and the parallel
``src.utils.stations._read_capped_json`` (used by the two
``@lru_cache``-decorated import-time loaders ``_station_entries`` and
``_vienna_polygons``). The 9 remaining stat-then-open sites
(``src/utils/cache.py`` × 3, ``src/places/quota.py``,
``src/places/tiling.py``, ``src/places/merge.py``,
``src/build_feed.py`` × 2, ``src/utils/stations_validation.py``) are
covered by the inventory walker test below.
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
from typing import Any

import pytest


_BASE_DIR = Path(__file__).resolve().parents[1]


# ============================================================================
# Helper: `path.stat()` lies, the actual file is huge.
# ============================================================================


class _LyingPath:
    """Path-like wrapper that returns a fake stat() result.

    Simulates the TOCTOU race: stat() reports a small size (the loader
    accepts the file), but the actual on-disk file is huge (open() will
    read more than max_bytes). Pre-fix, the loader buffers the whole big
    file via ``json.load``; post-fix, ``os.fstat`` on the open file
    descriptor reports the real size and the cap fires.
    """

    def __init__(self, real_path: Path, fake_size: int) -> None:
        self._real = real_path
        self._fake_size = fake_size

    def stat(self) -> os.stat_result:
        real = self._real.stat()
        # Re-emit the real stat with a forged st_size — pre-fix code
        # sees a small size and proceeds; post-fix code never consults
        # this path-level stat.
        return os.stat_result((
            real.st_mode, real.st_ino, real.st_dev, real.st_nlink,
            real.st_uid, real.st_gid,
            self._fake_size,  # ← lie about size
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


def _write_oversized(path: Path, size_bytes: int) -> None:
    """Write a flat-but-huge JSON list that exceeds the loader's byte cap."""
    payload = "[" + ",".join("0" for _ in range(size_bytes // 2)) + "]"
    path.write_text(payload, encoding="utf-8")


# ============================================================================
# PoC #1: read_capped_json (canonical helper, used by 16+ scripts)
# ============================================================================


def test_read_capped_json_resists_toctou_lying_stat(tmp_path: Path) -> None:
    """PoC: stat-time lie → open-time large file → must reject.

    Pre-fix: ``read_capped_json`` checks ``path.stat().st_size`` and is
    fooled by the lie, then opens the (real, oversized) file and
    propagates ``MemoryError`` past the catch tuple.

    Post-fix: opens the file first, then calls ``os.fstat`` on the file
    descriptor — which reports the *real* size — and rejects.
    """
    from src.utils.files import read_capped_json

    target = tmp_path / "data.json"
    # Write a 1 MiB file (well above any reasonable per-loader cap).
    _write_oversized(target, 1 * 1024 * 1024)

    # Wrap the path with a stat() that lies about the size.
    lying = _LyingPath(target, fake_size=10)

    # Pre-fix: would parse the 1 MiB file and return the huge list.
    # Post-fix: rejects via fstat on the open fd.
    result = read_capped_json(lying, max_bytes=1024, label="Test")  # type: ignore[arg-type]

    assert result is None, (
        "read_capped_json must reject oversized files even when path.stat() "
        "reports a forged small size — fstat on the opened file descriptor "
        "is the only TOCTOU-safe size check."
    )


def test_read_capped_json_accepts_correctly_sized_file(tmp_path: Path) -> None:
    """Regression: normal files below the cap still parse correctly."""
    from src.utils.files import read_capped_json

    target = tmp_path / "data.json"
    target.write_text(json.dumps({"a": 1, "b": [1, 2, 3]}), encoding="utf-8")

    result = read_capped_json(target, max_bytes=1 * 1024 * 1024, label="Test")
    assert result == {"a": 1, "b": [1, 2, 3]}


def test_read_capped_json_accepts_utf8_bom(tmp_path: Path) -> None:
    """Regression: UTF-8 BOM handling preserved by the binary-mode read.

    ``json.loads`` accepts BOM-prefixed bytes natively; the post-fix
    binary-mode read must preserve this contract so operator-supplied
    JSON files written by Windows tooling still parse.
    """
    import codecs

    from src.utils.files import read_capped_json

    target = tmp_path / "data.json"
    target.write_bytes(codecs.BOM_UTF8 + b'{"x": 1}')

    result = read_capped_json(target, max_bytes=1024, label="Test")
    assert result == {"x": 1}


# ============================================================================
# PoC #2: stations.py:_read_capped_json (private helper, @lru_cache import-time)
# ============================================================================


def test_stations_read_capped_json_resists_toctou_lying_stat(tmp_path: Path) -> None:
    """PoC for the parallel private helper in ``src.utils.stations``.

    Same TOCTOU shape as the canonical helper above — except this site
    feeds the two ``@lru_cache``-decorated import-time loaders
    (``_station_entries``, ``_vienna_polygons``), so a successful
    bypass crashes EVERY feed-build path that touches a station name
    or a Vienna geo-fence check (which is essentially every code path
    in the repo).
    """
    from src.utils.stations import _read_capped_json

    target = tmp_path / "stations.json"
    _write_oversized(target, 1 * 1024 * 1024)

    lying = _LyingPath(target, fake_size=10)

    result = _read_capped_json(
        lying, max_bytes=1024, label="Test"  # type: ignore[arg-type]
    )

    assert result is None, (
        "stations._read_capped_json must reject oversized files even when "
        "path.stat() lies about the size — closes the TOCTOU window between "
        "stat and open at the @lru_cache import-time loader sites."
    )


# ============================================================================
# PoC #3: defense-in-depth read cap (special files like /dev/zero)
# ============================================================================


def test_read_capped_json_resists_zero_size_special_file(tmp_path: Path) -> None:
    """PoC: a regular file whose ``stat().st_size`` reports 0 but whose
    bytes-on-read exceed the cap.

    Special files like ``/dev/zero`` and ``/dev/random`` always report
    ``st_size == 0`` (and always have, by POSIX contract). An attacker
    who can swap the loader target via symlink (or a TOCTOU-then-symlink
    sequence) can route the parser at an unbounded byte source. The
    fstat check alone passes (size is 0, ≤ cap); the defense-in-depth
    ``handle.read(max_bytes + 1)`` cap closes that gap by truncating
    the read at the cap and rejecting if more bytes arrive.

    We simulate the special-file contract by lying about ``st_size``
    being zero while the real file holds 100 KiB of bytes.
    """
    from src.utils.files import read_capped_json

    target = tmp_path / "special.json"
    target.write_text("[" + ",".join("0" for _ in range(50_000)) + "]", encoding="utf-8")

    # The ``_LyingPath`` returns ``st_size=0`` for both stat and the
    # underlying fstat path (we patch fstat below).

    real_fstat = os.fstat

    def fake_fstat(fd: int) -> os.stat_result:
        # Simulate /dev/zero / FIFO contract: st_size always reports 0.
        real = real_fstat(fd)
        return os.stat_result((
            real.st_mode, real.st_ino, real.st_dev, real.st_nlink,
            real.st_uid, real.st_gid,
            0,  # ← zero, but the file has bytes
            real.st_atime, real.st_mtime, real.st_ctime,
        ))

    # Patch os.fstat globally so the helper's check is bypassed; only
    # the defense-in-depth read-cap can save us.
    original_fstat = os.fstat
    os.fstat = fake_fstat
    try:
        result = read_capped_json(target, max_bytes=1024, label="Test")
    finally:
        os.fstat = original_fstat

    assert result is None, (
        "read_capped_json must reject the file when the on-disk byte "
        "count exceeds max_bytes, even when fstat reports st_size=0 "
        "(special-file contract). The defense-in-depth read-cap is "
        "the only protection against zero-sized special files."
    )


# ============================================================================
# Inventory walker: detect future drift across all stat-then-open sites
# ============================================================================


def _find_unsafe_stat_then_open(tree: ast.Module, source: str) -> list[tuple[int, str]]:
    """Walk an AST and return (lineno, snippet) tuples for any pattern
    where ``<expr>.stat().st_size`` is followed by ``<expr>.open(...)``
    in the same function/method body without an intervening ``os.fstat``
    call. Used by the inventory test to flag any new TOCTOU-vulnerable
    loader added in a future PR.
    """

    findings: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        # Look at all top-level statements + nested try/with bodies.
        sees_stat_st_size = False
        sees_fstat = False
        for sub in ast.walk(node):
            # Detect ``something.stat().st_size`` access (Attribute on a Call).
            if (
                isinstance(sub, ast.Attribute)
                and sub.attr == "st_size"
                and isinstance(sub.value, ast.Call)
                and isinstance(sub.value.func, ast.Attribute)
                and sub.value.func.attr == "stat"
            ):
                sees_stat_st_size = True
            # Detect ``os.fstat(...)`` calls.
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "fstat"
            ):
                sees_fstat = True
        if sees_stat_st_size and not sees_fstat:
            findings.append((node.lineno, node.name))
    return findings


def test_no_function_uses_unsafe_stat_then_open_pattern() -> None:
    """Inventory walker: every loader that gates ``open()`` on
    ``path.stat().st_size`` MUST also call ``os.fstat`` on the open file
    descriptor. Catches any future PR that re-introduces the TOCTOU
    pattern at a new site.

    The walker scans ``src/utils/files.py``, ``src/utils/stations.py``,
    ``src/utils/cache.py``, ``src/utils/stations_validation.py``,
    ``src/places/quota.py``, ``src/places/tiling.py``,
    ``src/places/merge.py`` and ``src/build_feed.py`` — the eight
    modules currently containing the canonical
    ``MAX_*_FILE_BYTES`` cap pattern.
    """
    modules = [
        _BASE_DIR / "src" / "utils" / "files.py",
        _BASE_DIR / "src" / "utils" / "stations.py",
        _BASE_DIR / "src" / "utils" / "cache.py",
        _BASE_DIR / "src" / "utils" / "stations_validation.py",
        _BASE_DIR / "src" / "places" / "quota.py",
        _BASE_DIR / "src" / "places" / "tiling.py",
        _BASE_DIR / "src" / "places" / "merge.py",
        _BASE_DIR / "src" / "build_feed.py",
    ]

    findings: dict[Path, list[tuple[int, str]]] = {}
    for module in modules:
        source = module.read_text(encoding="utf-8")
        tree = ast.parse(source)
        unsafe = _find_unsafe_stat_then_open(tree, source)
        if unsafe:
            findings[module] = unsafe

    if findings:
        message_lines = [
            "Found stat-then-open TOCTOU pattern in the following functions:",
        ]
        for module, items in findings.items():
            for lineno, name in items:
                rel = module.relative_to(_BASE_DIR)
                message_lines.append(f"  {rel}:{lineno} in {name}()")
        message_lines.append(
            "Each function gates open() on path.stat().st_size but never "
            "calls os.fstat on the open file descriptor — the TOCTOU "
            "window between stat and open lets an attacker swap the "
            "inode (atomic os.replace, symlink swap) to bypass the cap."
        )
        pytest.fail("\n".join(message_lines))


# ============================================================================
# Precondition: the canonical cap constants exist
# ============================================================================


def test_precondition_canonical_cap_constants_exist() -> None:
    """Pin the cap constants the post-fix code reads. If a future
    refactor renames them, every other test in this file would silently
    pass even on unfixed code.
    """
    from src.utils import files
    from src.utils import stations
    from src.utils import cache
    from src.places import quota, tiling
    from src import build_feed

    assert isinstance(files.DEFAULT_MAX_JSON_FILE_BYTES, int)
    assert files.DEFAULT_MAX_JSON_FILE_BYTES > 0

    # ``MAX_STATIONS_FILE_BYTES`` is defined in ``src.utils.stations`` and
    # re-imported by ``src.places.merge`` and
    # ``src.utils.stations_validation`` — the canonical home of the
    # constant is ``stations`` so we pin it there (mypy --strict on the
    # re-export sites complains about implicit re-export).
    assert isinstance(stations.MAX_STATIONS_FILE_BYTES, int)
    assert stations.MAX_STATIONS_FILE_BYTES > 0
    assert isinstance(stations.MAX_VIENNA_POLYGON_FILE_BYTES, int)
    assert stations.MAX_VIENNA_POLYGON_FILE_BYTES > 0

    assert isinstance(cache.MAX_CACHE_FILE_BYTES, int)
    assert cache.MAX_CACHE_FILE_BYTES > 0

    assert isinstance(quota.MAX_QUOTA_FILE_BYTES, int)
    assert quota.MAX_QUOTA_FILE_BYTES > 0

    assert isinstance(tiling.MAX_TILE_FILE_BYTES, int)
    assert tiling.MAX_TILE_FILE_BYTES > 0

    assert isinstance(build_feed.MAX_STATE_FILE_BYTES, int)
    assert build_feed.MAX_STATE_FILE_BYTES > 0
