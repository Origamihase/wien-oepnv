"""Data-loss floor for the ÖBB station-directory rebuild.

A truncated / corrupt ÖBB workbook yields zero or very few ÖBB stations, which
would otherwise silently overwrite the populated ``data/stations.json`` with a
manual-only (or near-empty) set. The floor refuses such a write and keeps the
pinned committed file.
"""
from __future__ import annotations

from scripts import update_station_directory


def test_zero_oebb_stations_is_always_rejected() -> None:
    # 0 ÖBB stations is always a failure — even on a fresh run with no existing
    # directory to protect (a valid workbook always yields stations).
    fv = update_station_directory._station_directory_floor_violation
    assert fv(new_oebb=0, existing_oebb=2237) is not None
    assert fv(new_oebb=0, existing_oebb=0) is not None


def test_drastic_shrink_against_populated_directory_is_rejected() -> None:
    # Manual-only fallback (~296) replacing a populated directory (~2237).
    fv = update_station_directory._station_directory_floor_violation
    assert fv(new_oebb=296, existing_oebb=2237) is not None


def test_normal_run_writes_through() -> None:
    fv = update_station_directory._station_directory_floor_violation
    assert fv(new_oebb=2200, existing_oebb=2237) is None
    # The floor uses a strict ``<`` comparison, so exactly at the ratio passes.
    ratio = update_station_directory.STATION_DIRECTORY_FLOOR_RATIO
    boundary = int(2237 * ratio)
    assert fv(new_oebb=boundary + 1, existing_oebb=2237) is None


def test_fresh_directory_writes_through() -> None:
    # No existing directory (existing_oebb == 0) and a non-empty result →
    # nothing to lose, so the write proceeds.
    fv = update_station_directory._station_directory_floor_violation
    assert fv(new_oebb=1500, existing_oebb=0) is None
