"""Sentinel PoC: JSON size-bomb defence across on-disk parsers.

Threat model
------------
The 2026-05-08 round of JSON depth-bomb defences canonicalised the
``except (ValueError, json.JSONDecodeError, RecursionError)`` pattern for
every on-disk JSON parser. ``RecursionError`` covers the *deeply-nested*
attack shape, but a structurally orthogonal attack — a wide-but-shallow
JSON document such as ``[1, 1, 1, … (50 million times) … 1]`` — slips
past the depth-bomb defences entirely:

  * ``json.loads`` does NOT raise ``RecursionError`` on a flat list
    regardless of length, so the existing depth-bomb catch tuple is
    irrelevant.
  * The parsing path allocates one Python ``int`` (28 bytes) per element
    plus list overhead (8 bytes per slot), so a 50 MiB on-disk file
    blows up to ~500 MiB resident memory; a 1 GiB file pushes past the
    process's `ulimit -v` and crashes with ``MemoryError``.
  * ``MemoryError`` is a ``BaseException`` subclass — it is NOT caught
    by any of the surrounding ``except (OSError, json.JSONDecodeError,
    RecursionError)`` handlers in the cache / quota / tile loaders, so
    the unhandled exception propagates out of the loader, escapes the
    feed orchestrator's main ``try`` block, and crashes the entire
    cron-driven build.

The threat actor is the same as the depth-bomb family: a compromised CI
runner / partial flush + power loss / operator mis-edit that drops a
multi-MiB to multi-GiB file under ``cache/`` or ``data/places-*.json``.
The defence-in-depth contract this file pins is that every on-disk JSON
parser in the canonical loader set MUST short-circuit before
``json.load`` / ``json.loads`` is invoked when the underlying file
exceeds the per-loader byte cap.

The fix shape mirrors the per-loader depth-bomb catch: each parser
stat()'s the file BEFORE opening / reading, and treats sizes above
``MAX_CACHE_FILE_BYTES`` (50 MiB for the cache/status loaders) /
``MAX_QUOTA_FILE_BYTES`` (1 MiB for the Places quota counter, which is
a single small JSON object) / ``MAX_TILE_FILE_BYTES`` (1 MiB for the
Places tile config, an operator-supplied list of bounding boxes) as
unreadable / corrupt. Each cap is sized at ~100x the largest
legitimately-written file shape so the cap does NOT introduce a
false-positive rejection of valid state.

Sites covered
-------------
  * ``src/utils/cache.py:read_cache`` (cache-events loader)
  * ``src/utils/cache.py:read_status`` (status / heartbeat loader)
  * ``src/utils/cache.py:write_cache`` data-degradation guard
    (existing-cache reader before overwrite)
  * ``src/places/quota.py:MonthlyQuota.load`` (Places API quota counter)
  * ``src/places/tiling.py:load_tiles_from_file`` (Places tile config)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest


def _write_oversized(path: Path, size_bytes: int) -> None:
    """Write a flat-but-huge JSON list that exceeds the loader's byte cap.

    The payload shape ``[0,0,0,…]`` is intentional: it is BOTH a valid
    JSON document (so ``json.loads`` would succeed if it ran) AND wide
    enough to consume memory proportional to the file size. Pre-fix,
    every loader below would buffer the whole file via
    ``path.read_text()`` / ``json.load(fh)`` and consume O(file_size)
    memory; post-fix, the size cap rejects the file before opening.
    """
    payload = "[" + ",".join("0" for _ in range(size_bytes // 2)) + "]"
    path.write_text(payload, encoding="utf-8")


# ============================================================================
# Precondition: the canonical caps are exposed and sized for production
# ============================================================================


def test_precondition_cache_size_cap_constants_exist() -> None:
    """Pin the canonical cap constants. If a future refactor renames or
    removes them, every regression test below would silently pass even on
    unfixed code — so we pin the precondition first."""
    from src.utils import cache
    from src.places import quota, tiling

    assert isinstance(cache.MAX_CACHE_FILE_BYTES, int)
    assert cache.MAX_CACHE_FILE_BYTES > 0
    # Cap must accommodate the largest legitimate on-disk cache observed
    # in production; below 1 MiB would reject normal cache state.
    assert cache.MAX_CACHE_FILE_BYTES >= 1_000_000

    assert isinstance(quota.MAX_QUOTA_FILE_BYTES, int)
    assert quota.MAX_QUOTA_FILE_BYTES > 0

    assert isinstance(tiling.MAX_TILE_FILE_BYTES, int)
    assert tiling.MAX_TILE_FILE_BYTES > 0


# ============================================================================
# src/utils/cache.py — Cache events / status / write-guard
# ============================================================================


def test_read_cache_rejects_oversized_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pre-fix: ``read_cache`` opened the cache file via ``cache_file.open(\"r\")``
    and called ``json.load(fh)`` unconditionally. A planted huge file
    (e.g. 1 GiB of valid but wide JSON) buffered into memory and
    crashed via ``MemoryError``. Post-fix: the size cap rejects the
    file before ``open`` is called and ``read_cache`` returns the
    canonical empty list."""
    from src.utils import cache

    monkeypatch.setattr(cache, "_CACHE_DIR", tmp_path, raising=False)
    monkeypatch.setattr(cache, "MAX_CACHE_FILE_BYTES", 1024, raising=False)

    cache_file = cache._cache_file("wl")
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    _write_oversized(cache_file, 4096)

    caplog.set_level(logging.WARNING, logger="src.utils.cache")

    with patch("src.utils.cache.json.load") as mock_load:
        result = cache.read_cache("wl")

    # Post-fix: json.load is never reached because the size cap fires first.
    mock_load.assert_not_called()
    assert result == []
    assert any("zu groß" in r.getMessage() or "too large" in r.getMessage()
               for r in caplog.records)


def test_read_status_rejects_oversized_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same shape as ``read_cache`` for the heartbeat / status file."""
    from src.utils import cache

    monkeypatch.setattr(cache, "_CACHE_DIR", tmp_path, raising=False)
    monkeypatch.setattr(cache, "MAX_CACHE_FILE_BYTES", 1024, raising=False)

    status_file = cache._status_file("wl")
    status_file.parent.mkdir(parents=True, exist_ok=True)
    _write_oversized(status_file, 4096)

    with patch("src.utils.cache.json.load") as mock_load:
        result = cache.read_status("wl")

    mock_load.assert_not_called()
    assert result is None


def test_write_cache_degradation_guard_handles_oversized_existing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``write_cache``'s data-degradation guard reads the EXISTING cache
    before overwriting. Pre-fix: a huge planted file would crash
    ``write_cache`` via MemoryError before the new payload was written.
    Post-fix: the oversized file is treated as unreadable and the new
    payload is written successfully (matching the existing
    json.JSONDecodeError fallback shape)."""
    from src.utils import cache

    monkeypatch.setattr(cache, "_CACHE_DIR", tmp_path, raising=False)
    monkeypatch.setattr(cache, "MAX_CACHE_FILE_BYTES", 1024, raising=False)

    cache_file = cache._cache_file("wl")
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    _write_oversized(cache_file, 4096)

    new_items = [
        {
            "id": "test",
            "guid": "test-guid",
            "first_seen": "2025-01-01T00:00:00+00:00",
        }
    ]
    # Pre-fix: this would attempt to json.load the 4 KiB existing cache
    # against a 1 KiB cap → without the new size guard the planted-huge
    # path would crash. Post-fix: the cache is treated as overwriteable
    # without consulting the existing payload's degradation contract.
    cache.write_cache("wl", new_items)

    rewritten = json.loads(cache_file.read_text(encoding="utf-8"))
    assert isinstance(rewritten, list)
    assert len(rewritten) == 1


def test_read_cache_normal_file_unaffected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity check: the size cap must not reject normal-sized cache files."""
    from src.utils import cache

    monkeypatch.setattr(cache, "_CACHE_DIR", tmp_path, raising=False)
    cache_file = cache._cache_file("wl")
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps([{"id": 1}]), encoding="utf-8")

    assert cache.read_cache("wl") == [{"id": 1}]


# ============================================================================
# src/places/quota.py — Places API quota counter
# ============================================================================


def test_quota_load_rejects_oversized_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Places API monthly quota counter is a small JSON object
    (5-10 fields). Pre-fix, an attacker-planted multi-MiB file would
    propagate past ``json.loads`` into MemoryError. Post-fix: the
    1 MiB cap rejects oversized files before the read."""
    from src.places import quota

    monkeypatch.setattr(quota, "MAX_QUOTA_FILE_BYTES", 1024, raising=False)

    state_file = tmp_path / "places_quota.json"
    _write_oversized(state_file, 4096)

    with pytest.raises(ValueError, match="too large|zu groß"):
        quota.MonthlyQuota.load(state_file)


# ============================================================================
# src/places/tiling.py — Places tile configuration
# ============================================================================


def test_load_tiles_from_file_rejects_oversized_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Places tile config is a small operator-supplied JSON list of
    bounding boxes (tens of objects, never multi-MiB). Pre-fix, a
    planted huge file would propagate past ``json.loads`` into
    MemoryError. Post-fix: the 1 MiB cap rejects oversized files."""
    from src.places import tiling

    monkeypatch.setattr(tiling, "MAX_TILE_FILE_BYTES", 1024, raising=False)

    tile_file = tmp_path / "tiles.json"
    _write_oversized(tile_file, 4096)

    with pytest.raises(ValueError, match="too large|zu groß"):
        tiling.load_tiles_from_file(tile_file)
