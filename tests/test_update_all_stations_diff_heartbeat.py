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

    assert "stations.json — Diff-Bericht" in rendered
    assert "107 → 107 (Δ +0)" in rendered
    assert "_Keine._" in rendered  # each empty section emits the placeholder
    assert rendered.count("_Keine._") == 4  # all four sections empty


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
    """All four blocking categories surface in the auto-quarantine list."""
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


def test_collect_quarantine_identifiers_filters_global_sentinel() -> None:
    """The synthetic ``<global>`` identifier emitted for directory-wide
    provider issues must be filtered out — it does not correspond to a
    single station and cannot be auto-quarantined."""
    from src.utils.stations_validation import (
        CrossStationIDIssue,
        NamingIssue,
        ProviderIssue,
        SecurityIssue,
        ValidationReport,
    )

    report = ValidationReport(
        total_stations=4,
        duplicates=(),
        alias_issues=(),
        coordinate_issues=(),
        gtfs_issues=(),
        security_issues=(SecurityIssue(identifier="bst:11", name="S", reason="x"),),
        cross_station_id_issues=(
            CrossStationIDIssue(
                identifier="bst:12",
                name="C",
                alias="abc",
                colliding_identifier="bst:99",
                colliding_name="D",
                colliding_field="bst_code",
            ),
        ),
        provider_issues=(
            ProviderIssue(identifier="<global>", name="<global>", reason="<2 VOR"),
            ProviderIssue(identifier="bst:13", name="P", reason="bad VOR id"),
        ),
        naming_issues=(NamingIssue(identifier="bst:14", name="N", reason="dup"),),
        gtfs_stop_count=0,
    )

    ids = wrapper._collect_quarantine_identifiers(report)
    assert ids == {"bst:11", "bst:12", "bst:13", "bst:14"}
    assert "<global>" not in ids


def test_partition_stations_splits_by_identifier_match() -> None:
    """_partition_stations honours the same identifier shape the validator
    emits, so an entry whose ``_format_identifier`` matches a member of
    the quarantine set is moved to the quarantined bucket."""
    good = {"bst_id": 1, "bst_code": "A", "name": "Good A", "source": "vor"}
    bad = {"bst_id": 2, "bst_code": "B", "name": "Bad B", "source": "vor"}

    quarantine_ids = {"bst:2 / code:B / source:vor"}
    valid, quarantined = wrapper._partition_stations([good, bad], quarantine_ids)

    assert valid == [good]
    assert quarantined == [bad]


def test_wrapper_auto_quarantines_matching_stations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """End-to-end: a provider_issue with a matching identifier removes
    the offending entry, writes ``data/quarantine.json``, and exits 0."""
    from src.utils.stations_validation import (
        ProviderIssue,
        ValidationReport,
        _format_identifier,
    )

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    good_a: dict[str, Any] = {
        "bst_id": 1, "bst_code": "A", "name": "Good A", "source": "vor",
    }
    bad_b: dict[str, Any] = {
        "bst_id": 2, "bst_code": "B", "name": "Bad B", "source": "vor",
    }
    good_c: dict[str, Any] = {
        "bst_id": 3, "bst_code": "C", "name": "Good C", "source": "vor",
    }

    stations_path = data_dir / "stations.json"
    stations_path.write_text(
        json.dumps({"stations": [good_a, bad_b, good_c]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    bad_identifier = _format_identifier(bad_b)
    failing_report = ValidationReport(
        total_stations=3,
        duplicates=(),
        alias_issues=(),
        coordinate_issues=(),
        gtfs_issues=(),
        security_issues=(),
        cross_station_id_issues=(),
        provider_issues=(
            ProviderIssue(
                identifier=bad_identifier,
                name="Bad B",
                reason="Invalid bst_code for VOR: B",
            ),
        ),
        naming_issues=(),
        gtfs_stop_count=0,
    )

    monkeypatch.setattr(
        "scripts.update_all_stations.subprocess.run", lambda *a, **kw: None
    )
    monkeypatch.setattr(wrapper, "validate_stations", lambda *a, **kw: failing_report)

    quarantine_path = data_dir / "quarantine.json"
    # The wrapper now accepts ``--target`` / ``--heartbeat`` /
    # ``--diff-report`` / ``--quarantine`` CLI args so the test
    # redirects every output into ``tmp_path`` directly. The
    # legacy ``monkeypatch.setattr(_DEFAULT_*)`` + ``chdir`` pattern
    # is preserved as belt-and-suspenders so a future revert of the
    # CLI args still routes outputs into ``tmp_path``.
    monkeypatch.setattr(wrapper, "_DEFAULT_HEARTBEAT_PATH", tmp_path / "heartbeat.json")
    monkeypatch.setattr(wrapper, "_DEFAULT_DIFF_REPORT_PATH", tmp_path / "diff.md")
    monkeypatch.setattr(wrapper, "_DEFAULT_QUARANTINE_PATH", quarantine_path)
    monkeypatch.chdir(tmp_path)

    exit_code = wrapper.main([
        "--target", str(stations_path),
        "--heartbeat", str(tmp_path / "heartbeat.json"),
        "--diff-report", str(tmp_path / "diff.md"),
        "--quarantine", str(quarantine_path),
    ])
    assert exit_code == 0

    final_payload = json.loads(stations_path.read_text(encoding="utf-8"))
    assert isinstance(final_payload, dict) and "stations" in final_payload
    final_names = {entry["name"] for entry in final_payload["stations"]}
    assert final_names == {"Good A", "Good C"}, (
        f"Bad B should have been quarantined out of stations.json, got {final_names}"
    )

    assert quarantine_path.exists(), "quarantine.json should be written"
    quarantine_payload = json.loads(quarantine_path.read_text(encoding="utf-8"))
    assert quarantine_payload["count"] == 1
    assert len(quarantine_payload["stations"]) == 1
    quarantined_entry = quarantine_payload["stations"][0]
    assert quarantined_entry["name"] == "Bad B"
    assert quarantined_entry["identifier"] == bad_identifier
    assert quarantined_entry["entry"] == bad_b
    assert quarantined_entry["issues"] == [
        {"category": "provider", "reason": "Invalid bst_code for VOR: B"}
    ]
    assert "timestamp" in quarantine_payload


def test_wrapper_skips_quarantine_for_global_only_issue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A ``<global>``-only provider_issue cannot quarantine any station;
    the pipeline still proceeds and writes no quarantine file."""
    from src.utils.stations_validation import (
        ProviderIssue,
        ValidationReport,
    )

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    stations_path = data_dir / "stations.json"
    stations_path.write_text(
        json.dumps(
            {"stations": [{"bst_id": 1, "bst_code": "A", "name": "Good"}]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    global_only_report = ValidationReport(
        total_stations=1,
        duplicates=(),
        alias_issues=(),
        coordinate_issues=(),
        gtfs_issues=(),
        security_issues=(),
        cross_station_id_issues=(),
        provider_issues=(
            ProviderIssue(
                identifier="<global>",
                name="<global>",
                reason="Need at least two VOR entries",
            ),
        ),
        naming_issues=(),
        gtfs_stop_count=0,
    )

    monkeypatch.setattr(
        "scripts.update_all_stations.subprocess.run", lambda *a, **kw: None
    )
    monkeypatch.setattr(wrapper, "validate_stations", lambda *a, **kw: global_only_report)

    quarantine_path = data_dir / "quarantine.json"
    # See ``test_wrapper_auto_quarantines_matching_stations`` above for
    # the rationale of pinning the wrapper's outputs via both the new
    # CLI args and the legacy ``_DEFAULT_*``/``chdir`` belt-and-
    # suspenders so the test never touches the production paths.
    monkeypatch.setattr(wrapper, "_DEFAULT_HEARTBEAT_PATH", tmp_path / "heartbeat.json")
    monkeypatch.setattr(wrapper, "_DEFAULT_DIFF_REPORT_PATH", tmp_path / "diff.md")
    monkeypatch.setattr(wrapper, "_DEFAULT_QUARANTINE_PATH", quarantine_path)
    monkeypatch.chdir(tmp_path)

    exit_code = wrapper.main([
        "--target", str(stations_path),
        "--heartbeat", str(tmp_path / "heartbeat.json"),
        "--diff-report", str(tmp_path / "diff.md"),
        "--quarantine", str(quarantine_path),
    ])
    assert exit_code == 0
    assert not quarantine_path.exists(), (
        "quarantine.json must not be created when the only blocking issue is the "
        "<global> sentinel — there's no individual station to remove"
    )

    final_payload = json.loads(stations_path.read_text(encoding="utf-8"))
    assert final_payload["stations"][0]["name"] == "Good"


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

    # Redirect every wrapper output to tmp_path via the CLI args so
    # the live files stay untouched. The legacy ``_DEFAULT_*``
    # monkeypatches are kept as belt-and-suspenders for an
    # accidental future regression.
    target_path = tmp_path / "stations.json"
    target_path.write_text(
        json.dumps({"stations": []}, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(wrapper, "_DEFAULT_HEARTBEAT_PATH", tmp_path / "heartbeat.json")
    monkeypatch.setattr(wrapper, "_DEFAULT_DIFF_REPORT_PATH", tmp_path / "diff.md")

    exit_code = wrapper.main([
        "--target", str(target_path),
        "--heartbeat", str(tmp_path / "heartbeat.json"),
        "--diff-report", str(tmp_path / "diff.md"),
        "--quarantine", str(tmp_path / "quarantine.json"),
    ])
    assert exit_code == 0

    heartbeat_path = tmp_path / "heartbeat.json"
    assert heartbeat_path.exists()
    payload = json.loads(heartbeat_path.read_text(encoding="utf-8"))
    assert "timestamp" in payload
    assert payload["validation"]["naming_issues"] == 0
    assert payload["sub_scripts"], "expected per-script timing entries"

    diff_path = tmp_path / "diff.md"
    assert diff_path.exists()
    assert "stations.json — Diff-Bericht" in diff_path.read_text(encoding="utf-8")
