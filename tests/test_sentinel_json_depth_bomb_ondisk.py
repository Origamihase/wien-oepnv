"""Sentinel PoC: JSON depth-bomb defence across on-disk / env-source parsers.

The 2026-05-07 journal canonicalised the defence pattern
``except (ValueError, json.JSONDecodeError, RecursionError)`` for every JSON
parser whose payload comes from an untrusted upstream peer. The "two-bug
minimum" rule from that round mandates that the on-disk fallback parsers in
the same module gain the same catch — otherwise a depth-bomb attacker who
can plant a poisoned cache / data file on the runner just shifts the crash
to the on-disk path on the next run.

This file extends the canonical regression coverage to the on-disk and
env-source siblings that survived the prior round:

  * ``src/places/tiling.py:load_tiles_from_env`` (env-source, no try/except)
  * ``src/places/tiling.py:load_tiles_from_file`` (file-source, no try/except)
  * ``src/utils/cache.py:read_cache`` (file-source, missing RecursionError)
  * ``src/utils/cache.py:read_status`` (file-source, missing RecursionError)
  * ``src/utils/cache.py:write_cache`` data-degradation guard (file-source,
    missing RecursionError on the EXISTING-cache read path)
  * ``src/utils/stations.py:_vienna_polygons`` (file-source, missing
    RecursionError)
  * ``src/utils/stations.py:_station_entries`` (file-source, missing
    RecursionError)
  * ``src/places/quota.py:MonthlyQuota.load`` (file-source, no try/except)
  * ``src/places/merge.py:load_stations`` (file-source, no try/except)
  * ``scripts/update_station_directory.py:_parse_bounding_box``
    (env-source, missing RecursionError on the EXISTING json.JSONDecodeError
    catch)

Threat model: a deeply-nested but well-formed JSON document persisted to
disk by a previous corrupted run, planted by a compromised CI runner, or
encoded into an env override (``PLACES_TILES``, ``BOUNDINGBOX_VIENNA``).
``json.loads`` raises ``RecursionError`` (NOT a subclass of
``json.JSONDecodeError`` and NOT caught by ``except ValueError``); the
pre-fix code therefore propagated the exception out of the loader and
crashed the surrounding pipeline (``build_feed.py`` orchestrator,
``update_station_directory.py`` cron, ``fetch_google_places_stations.py``
nightly job).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest


DEEP_BOMB_STR = "[" * 5000 + "]" * 5000


def test_precondition_deep_bomb_raises_recursion_error() -> None:
    """Pin the precondition that ``json.loads`` raises ``RecursionError`` on
    a 5000-deep nested array under Python's default 1000-frame stack budget.
    If a future Python release lifts the limit such that this payload parses
    successfully, every regression test below would silently pass even on
    unfixed code — so we pin the precondition first."""
    with pytest.raises(RecursionError):
        json.loads(DEEP_BOMB_STR)


# ============================================================================
# src/places/tiling.py — Tile config (CRITICAL)
# ============================================================================
#
# Pre-fix: ``load_tiles_from_env`` and ``load_tiles_from_file`` had NO
# try/except around ``json.loads``. A depth-bomb in ``PLACES_TILES`` env or
# a tiles file propagated ``RecursionError`` out of ``_load_tiles_configuration``
# in ``update_station_directory.py`` (caller's ``except (OSError, ValueError)``
# does NOT catch ``RecursionError``) and ``fetch_google_places_stations.py``
# (caller's ``_build_runtime_config`` is wrapped in ``except Exception`` so
# the script wouldn't crash but would emit a confusing "Configuration error"
# instead of the canonical "Cannot load Places tile configuration" warning).
# Post-fix: both functions raise ``ValueError("Tile configuration is not
# valid JSON")``, matching the existing semantic contract (callers catch
# ``ValueError`` for malformed JSON).


def test_load_tiles_from_env_handles_depth_bomb() -> None:
    """Pre-fix: depth-bomb in ``PLACES_TILES`` raised RecursionError out of
    ``json.loads``. Post-fix: surfaces a clean ValueError."""
    from src.places import tiling

    with pytest.raises(ValueError, match="(?i)valid json|tile configuration"):
        tiling.load_tiles_from_env(DEEP_BOMB_STR)


def test_load_tiles_from_file_handles_depth_bomb(tmp_path: Path) -> None:
    """Pre-fix: depth-bomb tile file raised RecursionError out of
    ``json.loads``. Post-fix: surfaces a clean ValueError."""
    from src.places import tiling

    poisoned = tmp_path / "tiles.json"
    poisoned.write_text(DEEP_BOMB_STR, encoding="utf-8")

    with pytest.raises(ValueError, match="(?i)valid json|tile configuration"):
        tiling.load_tiles_from_file(poisoned)


# ============================================================================
# src/utils/cache.py — Cache read/write/status (HIGH)
# ============================================================================
#
# Pre-fix: ``read_cache``/``read_status`` caught ``(json.JSONDecodeError,
# OSError)`` and ``write_cache``'s data-degradation guard caught
# ``(json.JSONDecodeError, OSError)`` — none included ``RecursionError``.
# A depth-bomb in ``cache/<provider>/events.json`` (e.g. left over from a
# previous corrupted run, planted by a compromised CI runner, or written
# during a partial flush followed by a power-loss) propagated RecursionError
# out of the orchestrator's main ``try`` block and crashed the entire feed
# build. Post-fix: each catch tuple includes ``RecursionError`` so the
# canonical fallback (return [], log warning) runs.


def test_cache_read_cache_handles_depth_bomb(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    from src.utils import cache

    cache_file = cache._cache_file("wl")
    target_dir = tmp_path / cache_file.parent.name
    target_dir.mkdir(parents=True)
    (target_dir / "events.json").write_text(DEEP_BOMB_STR, encoding="utf-8")

    caplog.set_level(logging.WARNING, logger="cache")
    with patch.object(cache, "_CACHE_DIR", tmp_path):
        result = cache.read_cache("wl")

    assert result == []
    assert any("invalid JSON" in r.getMessage() for r in caplog.records)


def test_cache_read_status_handles_depth_bomb(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    from src.utils import cache

    cache_file = cache._cache_file("wl")
    target_dir = tmp_path / cache_file.parent.name
    target_dir.mkdir(parents=True)
    (target_dir / "last_run.json").write_text(DEEP_BOMB_STR, encoding="utf-8")

    caplog.set_level(logging.WARNING, logger="cache")
    with patch.object(cache, "_CACHE_DIR", tmp_path):
        result = cache.read_status("wl")

    assert result is None


def test_cache_write_cache_degradation_guard_handles_depth_bomb(tmp_path: Path) -> None:
    """The data-degradation guard at ``write_cache`` reads the EXISTING cache
    file before overwriting it. A depth-bomb in the existing file would
    pre-fix raise RecursionError that escaped the
    ``except (json.JSONDecodeError, OSError)`` clause. Post-fix: the
    canonical "ignore read errors" fallback runs and the new payload is
    written successfully."""
    from src.utils import cache

    cache_file = cache._cache_file("wl")
    target_dir = tmp_path / cache_file.parent.name
    target_dir.mkdir(parents=True)
    poisoned = target_dir / "events.json"
    poisoned.write_text(DEEP_BOMB_STR, encoding="utf-8")

    new_items = [
        {
            "id": "test",
            "guid": "test-guid",
            "first_seen": "2025-01-01T00:00:00+00:00",
        }
    ]
    with patch.object(cache, "_CACHE_DIR", tmp_path):
        # Pre-fix: this raised RecursionError. Post-fix: the existing
        # cache is treated as unreadable and overwritten with the new payload.
        cache.write_cache("wl", new_items)

    assert poisoned.exists()
    rewritten = json.loads(poisoned.read_text(encoding="utf-8"))
    assert isinstance(rewritten, list)
    assert len(rewritten) == 1


# ============================================================================
# src/utils/stations.py — Vienna polygon + station directory (HIGH)
# ============================================================================
#
# Pre-fix: ``_vienna_polygons`` and ``_station_entries`` caught
# ``(OSError, json.JSONDecodeError)`` but not ``RecursionError``. A depth-bomb
# in ``data/vienna_polygon.json`` or ``data/stations.json`` (these files are
# produced by ``update_station_directory.py`` from upstream OEBB/WL data;
# a poisoned upstream payload that survives the depth-bomb defence in
# ``update_station_directory.py:_parse_bounding_box`` would corrupt the
# cached file) propagated RecursionError out of ``@lru_cache``-decorated
# loaders, crashing every call site (build_feed station enrichment, station
# lookup helpers). Post-fix: catches RecursionError too and returns the
# documented empty-collection fallback.


def test_stations_vienna_polygons_handles_depth_bomb(tmp_path: Path) -> None:
    from src.utils import stations

    poisoned = tmp_path / "vienna_polygon.json"
    poisoned.write_text(DEEP_BOMB_STR, encoding="utf-8")

    with patch.object(stations, "_VIENNA_POLYGON_PATH", poisoned):
        stations._vienna_polygons.cache_clear()
        try:
            result = stations._vienna_polygons()
        finally:
            stations._vienna_polygons.cache_clear()

    assert result == ()


def test_stations_station_entries_handles_depth_bomb(tmp_path: Path) -> None:
    from src.utils import stations

    poisoned = tmp_path / "stations.json"
    poisoned.write_text(DEEP_BOMB_STR, encoding="utf-8")

    with patch.object(stations, "_STATIONS_PATH", poisoned):
        stations._station_entries.cache_clear()
        try:
            result = stations._station_entries()
        finally:
            stations._station_entries.cache_clear()

    assert result == ()


# ============================================================================
# src/places/quota.py — MonthlyQuota state (HIGH)
# ============================================================================
#
# Pre-fix: ``MonthlyQuota.load`` had NO try/except around ``json.loads``.
# A depth-bomb in the quota state file (planted by a compromised runner or
# a corrupted previous write) would propagate RecursionError out of
# ``MonthlyQuota.load``. The caller in
# ``fetch_google_places_stations.py:main`` does wrap this in ``except
# Exception``, so the script wouldn't crash with an unhandled traceback,
# but the canonical contract is to surface the failure as ``ValueError``
# alongside the other state-corruption errors so a future caller without
# the broad ``except Exception`` inherits a safe default.


def test_quota_load_handles_depth_bomb(tmp_path: Path) -> None:
    from src.places.quota import MonthlyQuota

    poisoned = tmp_path / "quota.json"
    poisoned.write_text(DEEP_BOMB_STR, encoding="utf-8")

    with pytest.raises(ValueError, match="(?i)valid json|quota state"):
        MonthlyQuota.load(poisoned)


# ============================================================================
# src/places/merge.py — Stations merge file (HIGH)
# ============================================================================
#
# Pre-fix: ``load_stations`` had NO try/except around ``json.loads``.
# A depth-bomb in the input stations file (e.g. an attacker-controlled
# operator-supplied path or a corrupted previous output) would propagate
# RecursionError out of ``load_stations``. Post-fix: surfaces a clean
# ValueError consistent with the existing "must contain a list or wrapped
# object" / "entries must be objects" messages.


def test_merge_load_stations_handles_depth_bomb(tmp_path: Path) -> None:
    from src.places.merge import load_stations

    poisoned = tmp_path / "stations.json"
    poisoned.write_text(DEEP_BOMB_STR, encoding="utf-8")

    with pytest.raises(ValueError, match="(?i)valid json|stations"):
        load_stations(poisoned)


# ============================================================================
# scripts/update_station_directory.py — _parse_bounding_box (env-source)
# ============================================================================
#
# Pre-fix: ``_parse_bounding_box`` caught only ``json.JSONDecodeError``.
# A depth-bomb in ``BOUNDINGBOX_VIENNA`` env override would propagate
# RecursionError out of ``json.loads``, escape the existing catch, and
# crash the entire ``update_station_directory.py`` cron pipeline.
# Post-fix: catch tuple includes RecursionError, raising the same
# ValueError("BOUNDINGBOX_VIENNA must be valid JSON") as malformed JSON.


def test_update_station_directory_parse_bounding_box_handles_depth_bomb() -> None:
    from scripts import update_station_directory

    with pytest.raises(ValueError, match="(?i)valid json"):
        update_station_directory._parse_bounding_box(DEEP_BOMB_STR)
