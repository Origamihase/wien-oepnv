"""Sentinel PoC: JSON depth-bomb defence Round 4 — sites Round 3 missed.

The 2026-05-07 Round 3 journal entry committed to closing every
``json.loads`` / ``json.load`` / ``response.json()`` site in ``src/`` and
``scripts/`` that lacked an ``except`` tuple containing ``RecursionError``.
The closing checklist named seven covered files (tiling, cache, stations,
quota, merge, _parse_bounding_box) but the *actual* enumeration grep across
``src/`` and ``scripts/`` returned **sixteen further on-disk parse sites**
that retained the pre-canonicalisation ``except (json.JSONDecodeError,
[OSError | FileNotFoundError])`` shape — none of which catch
``RecursionError``. Round 3 closed early before the named drift was
fully audited.

This file extends the canonical regression coverage to the surviving sites:

  * ``src/providers/vor.py:_load_station_name_map`` — **module-import
    time** loader (called at module scope via
    ``STATION_NAME_MAP = _load_station_name_map()``). A depth-bomb in
    ``data/vor-haltestellen.mapping.json`` crashes the WHOLE VOR provider
    import and the feed-build pipeline that imports it.
  * ``src/providers/vor.py:load_request_count`` — daily-quota counter
    read; depth-bomb propagates out of ``_QUOTA_LOCK``-guarded path.
  * ``src/providers/vor.py:save_request_count`` — daily-quota counter
    write under exclusive ``file_lock``; pre-fix the inner read raised
    ``RecursionError`` past the broad lock-error handler and crashed the
    cron run mid-quota-debit.
  * ``src/utils/stations_validation.py:_load_stations`` — validation
    utility used by ``scripts/validate_stations.py``.
  * ``scripts/enrich_station_aliases.py:_load_vor_mapping`` — VOR
    mapping loader.
  * ``scripts/enrich_station_aliases.py:_load_pendler_alternative_names``
    — pendler candidates loader.
  * ``scripts/enrich_station_aliases.py:main`` — top-level stations file
    parser.
  * ``scripts/update_station_directory.py:_load_existing_station_entries``
    — incremental refresh's existing-state reader.
  * ``scripts/update_station_directory.py:_load_vor_name_to_id_map`` —
    VOR mapping loader.
  * ``scripts/update_station_directory.py:load_pendler_station_ids``
    — pendler ID list loader.
  * ``scripts/update_station_directory.py:load_pendler_name_candidates``
    — pendler candidates loader.
  * ``scripts/update_wl_stations.py:load_vor_mapping`` — VOR mapping
    loader.
  * ``scripts/update_wl_stations.py:merge_into_stations`` — pre-merge
    existing-state read; pre-fix caught only ``FileNotFoundError`` so a
    depth-bomb (or even malformed JSON) crashed the merge.
  * ``scripts/update_all_stations.py:_load_stations`` — orchestrator's
    diff-detection reader.
  * ``scripts/fetch_google_places_stations.py:_parse_bounding_box`` —
    env-source ``BOUNDINGBOX_VIENNA`` parser; pre-fix had NO try/except
    so a depth-bomb in the env value escapes through ``_build_runtime
    _config``'s ``except Exception``-narrowed catch as a confusing
    "Configuration error" that masks the real cause.
  * ``scripts/validate_vor_mapping.py:main`` — diagnostic-only script
    completes the enumeration so the canonical contract holds repo-wide.

Threat model: a deeply-nested but well-formed JSON document persisted to
disk by a corrupted previous run, planted by a compromised CI runner, or
encoded into an env override. ``json.loads`` raises ``RecursionError``
(NOT a subclass of ``json.JSONDecodeError`` and NOT caught by ``except
ValueError`` / ``except OSError``); the pre-fix code therefore propagated
the exception out of the loader. Each test below first asserts the
canonical fallback runs (returns empty / raises documented domain
exception / overwrites corrupt cache) and never lets ``RecursionError``
escape.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from unittest.mock import patch

import pytest


DEEP_BOMB_STR = "[" * 5000 + "]" * 5000


def test_precondition_deep_bomb_raises_recursion_error() -> None:
    """Pin the precondition that ``json.loads`` raises ``RecursionError``."""
    with pytest.raises(RecursionError):
        json.loads(DEEP_BOMB_STR)


# ============================================================================
# src/providers/vor.py — _load_station_name_map (CRITICAL: module-import-time)
# ============================================================================
#
# Pre-fix: ``_load_station_name_map`` caught ``(FileNotFoundError,
# json.JSONDecodeError)`` only. The call site
# ``STATION_NAME_MAP = _load_station_name_map()`` runs unconditionally on
# ``import src.providers.vor`` — so a depth-bomb in
# ``data/vor-haltestellen.mapping.json`` raises ``RecursionError`` at
# module-import time, taking down every consumer (build_feed orchestrator,
# CLI, every script that imports the VOR provider). Post-fix: the catch
# tuple includes ``RecursionError`` and the function returns ``{}`` with
# a logged warning, mirroring the existing JSONDecodeError fallback.


def test_vor_load_station_name_map_handles_depth_bomb(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from src.providers import vor

    poisoned = tmp_path / "vor-haltestellen.mapping.json"
    poisoned.write_text(DEEP_BOMB_STR, encoding="utf-8")

    caplog.set_level(logging.WARNING)
    with patch.object(vor, "MAPPING_FILE", poisoned):
        result = vor._load_station_name_map()

    assert result == {}


# ============================================================================
# src/providers/vor.py — load_request_count / save_request_count (HIGH)
# ============================================================================
#
# Pre-fix: both functions caught ``(FileNotFoundError, OSError,
# json.JSONDecodeError)`` only. A depth-bomb in
# ``data/vor_request_count.json`` (planted by a compromised CI runner or
# left over from a partial flush + power-loss) would propagate
# ``RecursionError`` out of ``load_request_count`` (called from many sites
# in the VOR fetch pipeline) and out of ``save_request_count``'s inner
# read-back-under-lock. The latter is especially dangerous because
# ``save_request_count`` is called per-request and a depth-bomb in the
# counter file would crash the cron mid-quota-debit, potentially
# double-counting requests on the next run. Post-fix: both catch tuples
# include ``RecursionError`` so the canonical fallback (treat as
# unreadable, fall back to memory cache or zero) runs.


def test_vor_load_request_count_handles_depth_bomb(tmp_path: Path) -> None:
    from src.providers import vor

    poisoned = tmp_path / "vor_request_count.json"
    poisoned.write_text(DEEP_BOMB_STR, encoding="utf-8")

    with patch.object(vor, "REQUEST_COUNT_FILE", poisoned):
        # Force a bypass so we hit the file-read branch, not the memory cache.
        result = vor.load_request_count(bypass_cache=True)

    assert result == (None, 0)


def test_vor_save_request_count_handles_depth_bomb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.providers import vor

    poisoned = tmp_path / "vor_request_count.json"
    poisoned.write_text(DEEP_BOMB_STR, encoding="utf-8")

    # Force the inner flush branch so the inner json.loads runs.
    monkeypatch.setenv("WIEN_OEPNV_TEST_QUOTA_BATCH", "1")
    with patch.object(vor, "REQUEST_COUNT_FILE", poisoned):
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
    # Pre-fix: RecursionError escaped. Post-fix: the depth-bombed file is
    # treated as unreadable and the in-memory increment is committed.
    assert isinstance(result, int)
    assert result >= 1


# ============================================================================
# src/utils/stations_validation.py — _load_stations
# ============================================================================
#
# Pre-fix: ``_load_stations`` caught only ``json.JSONDecodeError`` and
# raised ``StationValidationError``. A depth-bomb propagates
# ``RecursionError`` out of the loader and crashes the validation
# script (``scripts/validate_stations.py``) with an unhandled traceback,
# masking the real cause. Post-fix: both errors raise the same
# ``StationValidationError`` so the validation script's clean exit path
# fires consistently.


def test_stations_validation_load_stations_handles_depth_bomb(tmp_path: Path) -> None:
    from src.utils import stations_validation

    poisoned = tmp_path / "stations.json"
    poisoned.write_text(DEEP_BOMB_STR, encoding="utf-8")

    with pytest.raises(stations_validation.StationValidationError):
        stations_validation._load_stations(poisoned)


# ============================================================================
# scripts/enrich_station_aliases.py — three loaders
# ============================================================================
#
# Pre-fix: all three loaders caught only ``json.JSONDecodeError``. The
# script runs in the ``update_all_stations.py`` cron pipeline via
# ``subprocess.run(check=True)``, so any unhandled ``RecursionError``
# raises ``CalledProcessError`` and aborts the entire station-directory
# refresh. Post-fix: each catch tuple includes ``RecursionError`` so the
# documented empty-default fallback runs (or, for the top-level main(),
# returns exit code 1 cleanly).


def test_enrich_load_vor_mapping_handles_depth_bomb(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from scripts import enrich_station_aliases

    poisoned = tmp_path / "vor-haltestellen.mapping.json"
    poisoned.write_text(DEEP_BOMB_STR, encoding="utf-8")

    caplog.set_level(logging.WARNING)
    result = enrich_station_aliases._load_vor_mapping(poisoned)

    assert result == {}


def test_enrich_load_pendler_alternative_names_handles_depth_bomb(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from scripts import enrich_station_aliases

    poisoned = tmp_path / "pendler_candidates.json"
    poisoned.write_text(DEEP_BOMB_STR, encoding="utf-8")

    caplog.set_level(logging.WARNING)
    result = enrich_station_aliases._load_pendler_alternative_names(poisoned)

    assert result == {}


def test_enrich_main_handles_depth_bomb_in_stations_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from scripts import enrich_station_aliases

    poisoned = tmp_path / "stations.json"
    poisoned.write_text(DEEP_BOMB_STR, encoding="utf-8")

    args = [
        "enrich_station_aliases",
        "--stations",
        str(poisoned),
        "--vor-stops",
        str(tmp_path / "missing_vor.csv"),
        "--vor-mapping",
        str(tmp_path / "missing_vor_mapping.json"),
        "--gtfs-stops",
        str(tmp_path / "missing_gtfs.txt"),
        "--pendler-candidates",
        str(tmp_path / "missing_pendler.json"),
        "--dry-run",
    ]
    monkeypatch.setattr("sys.argv", args)
    caplog.set_level(logging.ERROR)

    rc = enrich_station_aliases.main()
    assert rc == 1


# ============================================================================
# scripts/update_station_directory.py — four loaders
# ============================================================================
#
# Pre-fix: each loader caught some combination of ``FileNotFoundError`` /
# ``OSError`` / ``json.JSONDecodeError``, but never ``RecursionError``.
# Two of them (``load_pendler_station_ids``,
# ``_load_existing_station_entries``) live on the cron pipeline's hot
# path; the other two are reached for every station-directory refresh.
# Post-fix: each catch tuple includes ``RecursionError`` so the canonical
# fallback (return empty / raise documented ``ValueError``) runs.


def test_update_station_directory_load_existing_handles_depth_bomb(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from scripts import update_station_directory

    poisoned = tmp_path / "stations.json"
    poisoned.write_text(DEEP_BOMB_STR, encoding="utf-8")

    caplog.set_level(logging.WARNING)
    mapping, manual = update_station_directory._load_existing_station_entries(poisoned)

    assert mapping == {}
    assert manual == []


def test_update_station_directory_load_vor_name_to_id_handles_depth_bomb(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from scripts import update_station_directory

    poisoned = tmp_path / "vor-haltestellen.mapping.json"
    poisoned.write_text(DEEP_BOMB_STR, encoding="utf-8")

    caplog.set_level(logging.WARNING)
    result = update_station_directory._load_vor_name_to_id_map(poisoned)

    assert result == {}


def test_update_station_directory_load_pendler_station_ids_handles_depth_bomb(
    tmp_path: Path,
) -> None:
    from scripts import update_station_directory

    poisoned = tmp_path / "pendler_station_ids.json"
    poisoned.write_text(DEEP_BOMB_STR, encoding="utf-8")

    with pytest.raises(ValueError, match="(?i)invalid json|pendler"):
        update_station_directory.load_pendler_station_ids(poisoned)


def test_update_station_directory_load_pendler_name_candidates_handles_depth_bomb(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from scripts import update_station_directory

    poisoned = tmp_path / "pendler_candidates.json"
    poisoned.write_text(DEEP_BOMB_STR, encoding="utf-8")

    caplog.set_level(logging.WARNING)
    result = update_station_directory.load_pendler_name_candidates(poisoned)

    assert result == set()


# ============================================================================
# scripts/update_wl_stations.py — load_vor_mapping / merge_into_stations
# ============================================================================
#
# Pre-fix: ``load_vor_mapping`` caught ``(FileNotFoundError,
# json.JSONDecodeError)`` and ``merge_into_stations`` caught only
# ``FileNotFoundError`` (a depth-bomb crashed even on regular malformed
# JSON, never mind RecursionError). Post-fix: both catch tuples include
# ``RecursionError`` and ``merge_into_stations`` also gains the
# JSONDecodeError catch so a malformed stations.json starts fresh
# instead of crashing the WL merge.


def test_update_wl_load_vor_mapping_handles_depth_bomb(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from scripts import update_wl_stations

    poisoned = tmp_path / "vor-haltestellen.mapping.json"
    poisoned.write_text(DEEP_BOMB_STR, encoding="utf-8")

    caplog.set_level(logging.WARNING)
    result = update_wl_stations.load_vor_mapping(poisoned)

    assert result == {}


def test_update_wl_merge_into_stations_handles_depth_bomb(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from scripts import update_wl_stations

    poisoned = tmp_path / "stations.json"
    poisoned.write_text(DEEP_BOMB_STR, encoding="utf-8")

    caplog.set_level(logging.WARNING)
    # Pre-fix: a bare RecursionError escaped (or for a malformed-but-not-
    # depth-bombed file, json.JSONDecodeError escaped). Post-fix: the
    # depth-bombed file is treated as starting-fresh and an empty
    # wl_entries list is merged without error.
    update_wl_stations.merge_into_stations(poisoned, [])

    # Verify the file was overwritten with an empty merge result. The script
    # may emit either a bare list or a wrapped {"stations": []} object —
    # both shapes are valid in the documented stations.json contract.
    rewritten = json.loads(poisoned.read_text(encoding="utf-8"))
    if isinstance(rewritten, dict):
        assert rewritten.get("stations") == []
    else:
        assert isinstance(rewritten, list)


# ============================================================================
# scripts/update_all_stations.py — _load_stations (orchestrator diff)
# ============================================================================
#
# Pre-fix: caught ``(OSError, json.JSONDecodeError)`` only. The
# orchestrator uses this loader for the post-merge diff detection; a
# depth-bomb would propagate ``RecursionError`` out of ``main()``,
# crashing the orchestrator with an unhandled traceback after the
# merged stations file was already written. Post-fix: returns ``[]``
# so the orchestrator's diff step degrades gracefully (treats as
# "nothing to compare").


def test_update_all_stations_load_stations_handles_depth_bomb(tmp_path: Path) -> None:
    from scripts import update_all_stations

    poisoned = tmp_path / "stations.json"
    poisoned.write_text(DEEP_BOMB_STR, encoding="utf-8")

    result = update_all_stations._load_stations(poisoned)

    assert result == []


# ============================================================================
# scripts/fetch_google_places_stations.py — _parse_bounding_box (env)
# ============================================================================
#
# Pre-fix: ``_parse_bounding_box`` had NO try/except around
# ``json.loads(raw)``. A depth-bomb in ``BOUNDINGBOX_VIENNA`` env
# (intentional misconfig, leaked CI env, compromised secret store)
# propagates ``RecursionError`` out of the function. The caller
# ``_build_runtime_config`` is wrapped in ``except Exception`` so the
# script doesn't crash with an unhandled traceback, but it emits a
# confusing "Configuration error" warning that masks the real cause.
# Post-fix: surfaces a clean ``ValueError("BOUNDINGBOX_VIENNA must be
# valid JSON")`` matching the canonical contract from the sibling
# ``update_station_directory.py:_parse_bounding_box`` (Round 3).


def test_fetch_google_places_parse_bounding_box_handles_depth_bomb() -> None:
    from scripts import fetch_google_places_stations

    with pytest.raises(ValueError, match="(?i)valid json|bounding"):
        fetch_google_places_stations._parse_bounding_box(DEEP_BOMB_STR)


# ============================================================================
# scripts/validate_vor_mapping.py — main (diagnostic completeness)
# ============================================================================
#
# Pre-fix: caught only ``json.JSONDecodeError``. Lower blast radius
# (single-call diagnostic), but the canonical contract is for any
# json.loads to defend against depth-bomb. Post-fix: also catches
# ``RecursionError`` and exits 1 with a clean "JSON decode error"
# message instead of crashing with an unhandled traceback.


def test_validate_vor_mapping_main_handles_depth_bomb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts import validate_vor_mapping

    poisoned = tmp_path / "vor-haltestellen.mapping.json"
    poisoned.write_text(DEEP_BOMB_STR, encoding="utf-8")

    # The script hardcodes Path("data/vor-haltestellen.mapping.json"); chdir
    # into tmp_path so the relative path resolves to our poisoned file.
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "vor-haltestellen.mapping.json").write_text(
        DEEP_BOMB_STR, encoding="utf-8"
    )

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        rc = validate_vor_mapping.main()
    finally:
        os.chdir(cwd)

    assert rc == 1
