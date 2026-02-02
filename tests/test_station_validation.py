from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.utils.stations_validation import (
    AliasIssue,
    CoordinateIssue,
    DuplicateGroup,
    validate_stations,
)


@pytest.fixture()
def stations_file(tmp_path: Path) -> Path:
    path = tmp_path / "stations.json"
    entries = [
        {
            "bst_id": 1,
            "bst_code": "A1",
            "name": "Alpha",
            "aliases": ["Alpha", "A1"],
            "latitude": 48.1,
            "longitude": 16.1,
            "source": "oebb",
        },
        {
            "bst_id": 2,
            "bst_code": "B2",
            "name": "Beta",
            "aliases": ["Beta", "B2", "4900002"],
            "latitude": 48.1,
            "longitude": 16.1,
            "vor_id": "4900002",
            "source": "vor",
        },
        {
            "bst_id": 3,
            "bst_code": "C3",
            "name": "Gamma",
            "aliases": [],
            "latitude": 48.2,
            "longitude": 16.2,
            "source": "wl",
        },
        {
            "bst_id": 4,
            "bst_code": "D4",
            "name": "Delta",
            "latitude": 48.3,
            "longitude": 16.3,
            "source": "wl",
            "vor_id": "missing",
        },
    ]
    path.write_text(json.dumps({"stations": entries}), encoding="utf-8")
    return path


@pytest.fixture()
def gtfs_file(tmp_path: Path) -> Path:
    path = tmp_path / "stops.txt"
    path.write_text("stop_id,stop_name\n4900002,Beta\n", encoding="utf-8")
    return path


def test_validation_detects_duplicates_and_alias_issues(stations_file: Path, gtfs_file: Path) -> None:
    report = validate_stations(stations_file, gtfs_stops_path=gtfs_file)

    assert report.total_stations == 4
    assert isinstance(report.duplicates[0], DuplicateGroup)
    duplicate_ids = report.duplicates[0].identifiers
    assert any("bst:1" in identifier for identifier in duplicate_ids)
    assert any("bst:2" in identifier for identifier in duplicate_ids)

    alias_issue_identifiers = {issue.identifier for issue in report.alias_issues}
    assert any("bst:3" in identifier for identifier in alias_issue_identifiers)

    gtfs_issue_identifiers = {issue.identifier for issue in report.gtfs_issues}
    assert any("bst:4" in identifier for identifier in gtfs_issue_identifiers)


def test_validation_without_gtfs_file_reports_zero_stops(stations_file: Path, tmp_path: Path) -> None:
    report = validate_stations(stations_file, gtfs_stops_path=tmp_path / "missing.txt")

    assert report.gtfs_stop_count == 0
    assert not report.gtfs_issues


def test_markdown_rendering_contains_sections(stations_file: Path, gtfs_file: Path) -> None:
    report = validate_stations(stations_file, gtfs_stops_path=gtfs_file)
    markdown = report.to_markdown()

    assert "# Stations Validation Report" in markdown
    assert "## Geographic duplicates" in markdown
    assert "## Alias issues" in markdown
    assert "## GTFS mismatches" in markdown
    assert "*Coordinate anomalies*:" in markdown


def test_report_flags_missing_alias_list(tmp_path: Path, gtfs_file: Path) -> None:
    stations = [
        {
            "bst_id": 10,
            "bst_code": "X10",
            "name": "Example",
            "latitude": 48.5,
            "longitude": 16.5,
            "source": "oebb",
        }
    ]
    path = tmp_path / "stations.json"
    path.write_text(json.dumps({"stations": stations}), encoding="utf-8")

    report = validate_stations(path, gtfs_stops_path=gtfs_file)
    assert report.alias_issues == (
        AliasIssue(
            identifier="bst:10 / code:X10 / source:oebb",
            name="Example",
            reason="missing aliases list",
        ),
    )


def test_coordinate_validation_detects_missing_and_out_of_bounds(tmp_path: Path) -> None:
    stations = [
        {
            "bst_id": 20,
            "bst_code": "Y20",
            "name": "Missing", 
            "longitude": 16.5,
            "aliases": ["Missing"],
        },
        {
            "bst_id": 21,
            "bst_code": "Y21",
            "name": "Swapped",
            "latitude": 16.6,
            "longitude": 48.2,
            "aliases": ["Swapped"],
        },
    ]
    path = tmp_path / "stations.json"
    path.write_text(json.dumps({"stations": stations}), encoding="utf-8")

    report = validate_stations(path)

    assert report.coordinate_issues == (
        CoordinateIssue(
            identifier="bst:20 / code:Y20",
            name="Missing",
            reason="missing latitude",
        ),
        CoordinateIssue(
            identifier="bst:21 / code:Y21",
            name="Swapped",
            reason="coordinates look swapped (lat=16.6, lon=48.2)",
        ),
    )
