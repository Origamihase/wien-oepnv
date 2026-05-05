from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.utils.stations_validation import (
    AliasIssue,
    CoordinateIssue,
    CrossStationIDIssue,
    DuplicateGroup,
    NamingIssue,
    ValidationReport,
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
    path.write_text(json.dumps(entries), encoding="utf-8")
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
    path.write_text(json.dumps(stations), encoding="utf-8")

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
    path.write_text(json.dumps(stations), encoding="utf-8")

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


def test_markdown_rendering_contains_cross_station_id_section() -> None:
    """to_markdown() must render cross_station_id_issues in counts and detail."""
    issue = CrossStationIDIssue(
        identifier="bst:1234",
        name="Wien Mitte",
        alias="Mitte",
        colliding_identifier="bst:5678",
        colliding_name="Praterstern",
        colliding_field="bst_code",
    )
    report = ValidationReport(
        total_stations=2,
        duplicates=(),
        alias_issues=(),
        coordinate_issues=(),
        gtfs_issues=(),
        security_issues=(),
        cross_station_id_issues=(issue,),
        provider_issues=(),
        naming_issues=(),
        gtfs_stop_count=0,
    )

    markdown = report.to_markdown()

    assert "*Cross station ID issues*: 1" in markdown
    assert "## Cross station ID issues" in markdown
    assert "bst:1234" in markdown
    assert "'Mitte'" in markdown
    assert "bst_code" in markdown
    assert "bst:5678" in markdown
    assert "No issues detected." not in markdown


def test_naming_validation_detects_duplicate_canonical_names(tmp_path: Path) -> None:
    """Two entries with the same ``name`` field must trigger a NamingIssue."""
    stations = [
        {
            "bst_id": 100,
            "bst_code": "X1",
            "name": "Wien Beispiel",
            "aliases": ["Wien Beispiel"],
            "latitude": 48.2,
            "longitude": 16.4,
            "source": "oebb",
        },
        {
            "bst_id": 200,
            "bst_code": "X2",
            "name": "Wien Beispiel",
            "aliases": ["Wien Beispiel"],
            "latitude": 48.21,
            "longitude": 16.41,
            "source": "wl",
        },
    ]
    path = tmp_path / "stations.json"
    path.write_text(json.dumps(stations), encoding="utf-8")

    report = validate_stations(path)
    naming_reasons = {issue.reason for issue in report.naming_issues}
    assert any("not unique" in reason for reason in naming_reasons)
    assert {issue.identifier for issue in report.naming_issues} == {
        "bst:100 / code:X1 / source:oebb",
        "bst:200 / code:X2 / source:wl",
    }


def test_naming_validation_detects_whitespace_in_source(tmp_path: Path) -> None:
    """Comma-separated source tokens must not carry whitespace."""
    stations = [
        {
            "bst_id": 300,
            "bst_code": "Y1",
            "name": "Wien Sauber",
            "aliases": ["Wien Sauber"],
            "latitude": 48.2,
            "longitude": 16.4,
            "source": "google_places, vor",
        },
        {
            "bst_id": 301,
            "bst_code": "Y2",
            "name": "Wien Ohne",
            "aliases": ["Wien Ohne"],
            "latitude": 48.21,
            "longitude": 16.41,
            "source": "google_places,vor",
        },
    ]
    path = tmp_path / "stations.json"
    path.write_text(json.dumps(stations), encoding="utf-8")

    report = validate_stations(path)
    whitespace_issues = [
        issue for issue in report.naming_issues if "whitespace" in issue.reason
    ]
    assert len(whitespace_issues) == 1
    assert whitespace_issues[0].identifier == "bst:300 / code:Y1 / source:google_places, vor"


def test_naming_validation_clean_data_yields_no_issues(tmp_path: Path) -> None:
    stations = [
        {
            "bst_id": 400,
            "bst_code": "Z1",
            "name": "Wien Eindeutig",
            "aliases": ["Wien Eindeutig"],
            "latitude": 48.2,
            "longitude": 16.4,
            "source": "oebb",
            "in_vienna": True,
            "pendler": False,
        },
    ]
    path = tmp_path / "stations.json"
    path.write_text(json.dumps(stations), encoding="utf-8")

    report = validate_stations(path)
    assert report.naming_issues == ()
    assert isinstance(NamingIssue("x", "y", "z"), NamingIssue)


def test_naming_validation_flags_in_vienna_and_pendler_combination(tmp_path: Path) -> None:
    """A station classified both as in_vienna and pendler is invalid.

    The two flags partition the directory: every entry is either inside
    the Vienna city limits (in_vienna=true) or a commuter-belt station
    outside (pendler=true), never both.
    """
    stations = [
        {
            "bst_id": 500,
            "bst_code": "Q1",
            "name": "Wien Verwirrt",
            "aliases": ["Wien Verwirrt"],
            "latitude": 48.2,
            "longitude": 16.4,
            "source": "oebb",
            "in_vienna": True,
            "pendler": True,
        },
    ]
    path = tmp_path / "stations.json"
    path.write_text(json.dumps(stations), encoding="utf-8")

    report = validate_stations(path)
    flag_issues = [i for i in report.naming_issues if "mutually exclusive" in i.reason]
    assert len(flag_issues) == 1
    assert flag_issues[0].name == "Wien Verwirrt"


def test_naming_validation_flags_neither_vienna_nor_pendler(tmp_path: Path) -> None:
    """A regular station with neither flag is invalid; only manual_foreign_city is exempt."""
    stations = [
        {
            "bst_id": 600,
            "bst_code": "Q2",
            "name": "Niemandsland",
            "aliases": ["Niemandsland"],
            "latitude": 48.5,
            "longitude": 16.7,
            "source": "oebb",
            "in_vienna": False,
            "pendler": False,
        },
        {
            "name": "Roma Termini",
            "aliases": ["Roma Termini"],
            "latitude": 41.901,
            "longitude": 12.500,
            "source": "manual",
            "type": "manual_foreign_city",
            "in_vienna": False,
            "pendler": False,
        },
    ]
    path = tmp_path / "stations.json"
    path.write_text(json.dumps(stations), encoding="utf-8")

    report = validate_stations(path)
    issues = [i for i in report.naming_issues if "both false" in i.reason]
    assert len(issues) == 1
    assert issues[0].name == "Niemandsland", "manual_foreign_city must be exempt"


def test_stations_json_has_mutually_exclusive_flags() -> None:
    """Lock the live data: no entry has both flags or none (except foreign cities)."""
    repo_root = Path(__file__).resolve().parent.parent
    with (repo_root / "data" / "stations.json").open(encoding="utf-8") as handle:
        data = json.load(handle)
    stations = data["stations"] if isinstance(data, dict) else data

    both = [s for s in stations if s.get("in_vienna") and s.get("pendler")]
    assert not both, (
        "Stations with both in_vienna and pendler are invalid: "
        + ", ".join(s.get("name", "<unnamed>") for s in both)
    )
    neither = [
        s for s in stations
        if not s.get("in_vienna") and not s.get("pendler") and s.get("type") != "manual_foreign_city"
    ]
    assert not neither, (
        "Stations with neither flag (and not manual_foreign_city) are invalid: "
        + ", ".join(s.get("name", "<unnamed>") for s in neither)
    )
