"""A dead source of the weekly station run turns the health check red.

The station run keeps the last good copy under ``data/`` when a download
fails, so the run itself stays green. Each good answer stamps ``checked_at``
into the copy's ``.cache.json`` sidecar; ``check_station_sources`` reads the
stamps (2026-10-25).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import scripts.health_check as hc

_NOW = datetime(2026, 10, 25, 21, 0, tzinfo=UTC)


def _stamp_all(root: Path, when: datetime, key: str = "checked_at") -> None:
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    for _, filename in hc.STATION_SOURCES:
        (data / f"{filename}.cache.json").write_text(
            json.dumps({"version": 1, key: when.isoformat()}), encoding="utf-8"
        )


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(hc, "REPO_ROOT", tmp_path)
    return tmp_path


def test_fresh_stamps_are_green(repo: Path) -> None:
    _stamp_all(repo, _NOW - timedelta(days=7))
    assert hc.check_station_sources(_NOW).ok


def test_one_failed_weekly_run_is_tolerated(repo: Path) -> None:
    _stamp_all(repo, _NOW - timedelta(days=14))
    assert hc.check_station_sources(_NOW).ok


def test_two_failed_weekly_runs_turn_red_and_name_the_source(repo: Path) -> None:
    _stamp_all(repo, _NOW - timedelta(days=2))
    sidecar = repo / "data" / "wienerlinien-ogd-haltepunkte.csv.cache.json"
    sidecar.write_text(
        json.dumps({"version": 1, "checked_at": (_NOW - timedelta(days=16)).isoformat()}),
        encoding="utf-8",
    )
    check = hc.check_station_sources(_NOW)
    assert not check.ok
    assert "WL-OGD Haltepunkte" in check.summary


def test_missing_stamp_is_red(repo: Path) -> None:
    _stamp_all(repo, _NOW)
    (repo / "data" / "oebb-verkehrsstationen.xlsx.cache.json").unlink()
    check = hc.check_station_sources(_NOW)
    assert not check.ok
    assert "ÖBB Verkehrsstationen" in check.summary


def test_old_sidecars_without_checked_at_use_fetched_at(repo: Path) -> None:
    _stamp_all(repo, _NOW - timedelta(days=1), key="fetched_at")
    assert hc.check_station_sources(_NOW).ok


def test_committed_sidecars_cover_every_source() -> None:
    root = Path(__file__).resolve().parents[1]
    for _, filename in hc.STATION_SOURCES:
        assert (root / "data" / filename).exists()
        assert (root / "data" / f"{filename}.cache.json").exists()
