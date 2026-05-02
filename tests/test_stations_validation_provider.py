"""Tests for VOR/OEBB provider validation in stations_validation.

These tests cover the logic that was previously inline in
``scripts/validate_stations.py:main()`` and is now in
``src.utils.stations_validation._find_provider_issues``.
"""
from __future__ import annotations

import json
from pathlib import Path

from src.utils.stations_validation import validate_stations


def _write(path: Path, entries: list[dict[str, object]]) -> None:
    path.write_text(json.dumps({"stations": entries}), encoding="utf-8")


def _vor_entry(bst: str, name: str, lat: float = 48.2, lon: float = 16.4) -> dict[str, object]:
    return {
        "name": name,
        "bst_id": bst,
        "bst_code": bst,
        "vor_id": bst,
        "aliases": [name, bst],
        "latitude": lat,
        "longitude": lon,
        "source": "vor",
    }


def _oebb_entry(bst_code: str, name: str, lat: float = 48.2, lon: float = 16.4) -> dict[str, object]:
    return {
        "name": name,
        "bst_id": "100",
        "bst_code": bst_code,
        "vor_id": "490000000",
        "aliases": [name, bst_code, "490000000"],
        "latitude": lat,
        "longitude": lon,
        "source": "oebb",
    }


def test_provider_issues_field_exists(tmp_path: Path) -> None:
    """Regression: ValidationReport must expose provider_issues."""
    path = tmp_path / "stations.json"
    _write(path, [_vor_entry("900100", "A"), _vor_entry("900200", "B", lat=48.3)])
    report = validate_stations(path)
    assert hasattr(report, "provider_issues")
    assert isinstance(report.provider_issues, tuple)


def test_clean_data_has_no_provider_issues(tmp_path: Path) -> None:
    path = tmp_path / "stations.json"
    _write(path, [_vor_entry("900100", "A"), _vor_entry("900200", "B", lat=48.3)])
    report = validate_stations(path)
    assert report.provider_issues == ()


def test_less_than_two_vor_entries_reports_global_issue(tmp_path: Path) -> None:
    path = tmp_path / "stations.json"
    _write(path, [_vor_entry("900100", "A")])
    report = validate_stations(path)
    reasons = [issue.reason for issue in report.provider_issues]
    assert "Need at least two VOR entries" in reasons
    # When the global check fails, the per-entry checks must be skipped to match
    # the early-return behaviour of the previous implementation.
    assert len(report.provider_issues) == 1


def test_zero_vor_entries_reports_global_issue(tmp_path: Path) -> None:
    path = tmp_path / "stations.json"
    _write(path, [_oebb_entry("Abc", "A"), _oebb_entry("Def", "B", lat=48.3)])
    report = validate_stations(path)
    reasons = [issue.reason for issue in report.provider_issues]
    assert reasons == ["Need at least two VOR entries"]


def test_invalid_vor_bst_id_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "stations.json"
    bad = _vor_entry("900100", "A")
    bad["bst_id"] = "not-a-vor-id"
    _write(path, [bad, _vor_entry("900200", "B", lat=48.3)])
    report = validate_stations(path)
    reasons = [issue.reason for issue in report.provider_issues]
    assert "Invalid bst_id for VOR: not-a-vor-id" in reasons


def test_invalid_vor_bst_code_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "stations.json"
    bad = _vor_entry("900100", "A")
    bad["bst_code"] = "not-a-vor-code"
    _write(path, [bad, _vor_entry("900200", "B", lat=48.3)])
    report = validate_stations(path)
    reasons = [issue.reason for issue in report.provider_issues]
    assert "Invalid bst_code for VOR: not-a-vor-code" in reasons


def test_missing_vor_bst_id_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "stations.json"
    bad = _vor_entry("900100", "A")
    del bad["bst_id"]
    _write(path, [bad, _vor_entry("900200", "B", lat=48.3)])
    report = validate_stations(path)
    reasons = [issue.reason for issue in report.provider_issues]
    assert "Invalid bst_id for VOR: None" in reasons


def test_five_digit_vor_id_is_accepted(tmp_path: Path) -> None:
    """Five-digit VOR ids like '93010' must validate (regression: see comment in module)."""
    path = tmp_path / "stations.json"
    _write(
        path,
        [
            _vor_entry("93010", "A"),
            _vor_entry("93011", "B", lat=48.3),
        ],
    )
    report = validate_stations(path)
    assert report.provider_issues == ()


def test_vor_bst_code_collides_with_oebb(tmp_path: Path) -> None:
    path = tmp_path / "stations.json"
    _write(
        path,
        [
            _vor_entry("900100", "VOR-A"),
            _vor_entry("900200", "VOR-B", lat=48.3),
            _oebb_entry("900100", "OEBB-conflict", lat=48.4),
        ],
    )
    report = validate_stations(path)
    reasons = [issue.reason for issue in report.provider_issues]
    assert "VOR bst_code collides with OEBB" in reasons


def test_source_string_with_spaces_is_parsed(tmp_path: Path) -> None:
    """`source: 'google_places, vor, wl'` (with whitespace after commas) must be recognised as VOR.

    This format appears in real production data (data/stations.json).
    """
    path = tmp_path / "stations.json"
    entry_a = _vor_entry("900100", "A")
    entry_a["source"] = "google_places, vor, wl"
    entry_b = _vor_entry("900200", "B", lat=48.3)
    entry_b["source"] = "google_places,vor"  # no whitespace variant
    _write(path, [entry_a, entry_b])
    report = validate_stations(path)
    assert report.provider_issues == ()


def test_real_data_has_no_provider_issues() -> None:
    """Regression test against the actual repo data."""
    path = Path("data/stations.json")
    report = validate_stations(path)
    assert report.provider_issues == (), (
        f"Unexpected provider issues in data/stations.json: {report.provider_issues}"
    )


def test_has_issues_flips_on_provider_issue(tmp_path: Path) -> None:
    """ValidationReport.has_issues must include provider_issues."""
    path = tmp_path / "stations.json"
    _write(path, [_vor_entry("900100", "A")])  # only 1 VOR entry → provider issue
    report = validate_stations(path)
    assert report.provider_issues
    assert report.has_issues is True
