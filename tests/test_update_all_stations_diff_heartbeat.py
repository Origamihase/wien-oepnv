"""Tests for the diff/heartbeat observability layer in update_all_stations."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts import update_all_stations as wrapper


def _entry(
    bst_id: str | None = None,
    name: str = "X",
    lat: float = 48.2,
    lon: float = 16.4,
) -> dict[str, Any]:
    e: dict[str, Any] = {"name": name, "latitude": lat, "longitude": lon}
    if bst_id is not None:
        e["bst_id"] = bst_id
    return e


def test_compute_diff_detects_added_and_removed() -> None:
    before = [_entry(bst_id="1", name="Alpha"), _entry(bst_id="2", name="Beta")]
    after = [_entry(bst_id="1", name="Alpha"), _entry(bst_id="3", name="Gamma")]

    diff = wrapper._compute_diff(before, after)

    assert [k for k, _ in diff["added"]] == ["bst:3"]
    assert [k for k, _ in diff["removed"]] == ["bst:2"]
    assert diff["renamed"] == []
    assert diff["coord_shifted"] == []


def test_compute_diff_detects_renames() -> None:
    before = [_entry(bst_id="1", name="Wien Westbf")]
    after = [_entry(bst_id="1", name="Wien Westbahnhof")]

    diff = wrapper._compute_diff(before, after)

    assert diff["renamed"] == [("bst:1", "Wien Westbf", "Wien Westbahnhof")]
    assert diff["added"] == []
    assert diff["removed"] == []


def test_compute_diff_detects_coord_shift_above_threshold() -> None:
    # Wien Aspern Nord coordinates: old GTFS value vs corrected VOR value (~1.16 km drift)
    before = [_entry(bst_id="4773541", name="Wien Aspern Nord", lat=48.234567, lon=16.520123)]
    after = [_entry(bst_id="4773541", name="Wien Aspern Nord", lat=48.234669, lon=16.504456)]

    diff = wrapper._compute_diff(before, after)

    assert len(diff["coord_shifted"]) == 1
    key, name, distance_m = diff["coord_shifted"][0]
    assert key == "bst:4773541"
    assert name == "Wien Aspern Nord"
    assert 1100 < distance_m < 1200  # ~1160 m


def test_compute_diff_ignores_sub_threshold_coord_shift() -> None:
    """A 50m drift must not be reported (signal-to-noise floor)."""
    before = [_entry(bst_id="1", name="X", lat=48.2000, lon=16.4000)]
    after = [_entry(bst_id="1", name="X", lat=48.2003, lon=16.4003)]  # ~40m

    diff = wrapper._compute_diff(before, after)
    assert diff["coord_shifted"] == []


def test_compute_diff_handles_no_bst_id_via_name_key() -> None:
    """Google-Places-only entries lack bst_id; they fall back to a name key."""
    before = [_entry(name="Stadtpark", lat=48.2024, lon=16.3791)]
    after = [_entry(name="Stadtpark", lat=48.2024, lon=16.3791)]
    after.append(_entry(name="Schwedenplatz", lat=48.2115, lon=16.378))

    diff = wrapper._compute_diff(before, after)
    assert [k for k, _ in diff["added"]] == ["name:Schwedenplatz"]
    assert diff["removed"] == []


def test_render_diff_markdown_clean_run_signals_no_change() -> None:
    """An empty diff should still produce a non-empty report — that's the heartbeat."""
    diff: wrapper._DiffResult = {"added": [], "removed": [], "renamed": [], "coord_shifted": []}
    rendered = wrapper._render_diff_markdown(diff, before_count=107, after_count=107, timestamp="2026-05-05T18:00:00+00:00")

    assert "stations.json Diff Report" in rendered
    assert "107 → 107 (Δ +0)" in rendered
    assert "_None._" in rendered  # each empty section emits the placeholder
    assert rendered.count("_None._") == 4  # all four sections empty


def test_render_diff_markdown_lists_renames() -> None:
    diff: wrapper._DiffResult = {
        "added": [],
        "removed": [],
        "renamed": [("bst:2511", "Wien Westbf", "Wien Westbahnhof")],
        "coord_shifted": [],
    }
    rendered = wrapper._render_diff_markdown(diff, 107, 107, "2026-05-05T00:00:00+00:00")
    assert '"Wien Westbf" → "Wien Westbahnhof"' in rendered


def test_load_stations_handles_wrapped_and_bare_lists(tmp_path: Path) -> None:
    """Both ``{"stations": [...]}`` and bare ``[...]`` are accepted (legacy tests)."""
    wrapped = tmp_path / "wrapped.json"
    wrapped.write_text(json.dumps({"stations": [{"name": "A"}]}))
    bare = tmp_path / "bare.json"
    bare.write_text(json.dumps([{"name": "B"}]))
    missing = tmp_path / "missing.json"  # never created

    assert wrapper._load_stations(wrapped) == [{"name": "A"}]
    assert wrapper._load_stations(bare) == [{"name": "B"}]
    assert wrapper._load_stations(missing) == []


def test_collect_blocking_issues_includes_naming_and_security() -> None:
    """Beyond the original provider/cross-station gates, naming + security
    issues now also block the commit (added in this PR)."""
    from src.utils.stations_validation import (
        NamingIssue,
        ProviderIssue,
        SecurityIssue,
        ValidationReport,
    )

    report = ValidationReport(
        total_stations=2,
        duplicates=(),
        alias_issues=(),
        coordinate_issues=(),
        gtfs_issues=(),
        security_issues=(SecurityIssue(identifier="bst:1", name="X", reason="unsafe char"),),
        cross_station_id_issues=(),
        provider_issues=(ProviderIssue(identifier="bst:2", name="Y", reason="bad"),),
        naming_issues=(NamingIssue(identifier="bst:3", name="Z", reason="not unique"),),
        gtfs_stop_count=0,
    )
    blocking = wrapper._collect_blocking_issues(report)
    categories = {cat for cat, _ in blocking}
    assert categories == {"provider", "naming", "security"}


def test_wrapper_writes_heartbeat_and_diff_on_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A successful run produces both observability artefacts."""
    from src.utils.stations_validation import ValidationReport

    monkeypatch.setattr(
        "scripts.update_all_stations.subprocess.run", lambda *a, **kw: None
    )
    clean_report = ValidationReport(
        total_stations=0,
        duplicates=(),
        alias_issues=(),
        coordinate_issues=(),
        gtfs_issues=(),
        security_issues=(),
        cross_station_id_issues=(),
        provider_issues=(),
        naming_issues=(),
        gtfs_stop_count=0,
    )
    monkeypatch.setattr(wrapper, "validate_stations", lambda *a, **kw: clean_report)

    # Redirect heartbeat + diff to tmp_path so the live files stay untouched
    monkeypatch.setattr(wrapper, "_DEFAULT_HEARTBEAT_PATH", tmp_path / "heartbeat.json")
    monkeypatch.setattr(wrapper, "_DEFAULT_DIFF_REPORT_PATH", tmp_path / "diff.md")

    exit_code = wrapper.main([])
    assert exit_code == 0

    heartbeat_path = tmp_path / "heartbeat.json"
    assert heartbeat_path.exists()
    payload = json.loads(heartbeat_path.read_text(encoding="utf-8"))
    assert "timestamp" in payload
    assert payload["validation"]["naming_issues"] == 0
    assert payload["sub_scripts"], "expected per-script timing entries"

    diff_path = tmp_path / "diff.md"
    assert diff_path.exists()
    assert "stations.json Diff Report" in diff_path.read_text(encoding="utf-8")
