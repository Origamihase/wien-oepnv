import json
import pytest
from unittest.mock import patch
from src.utils.stations_validation import validate_stations, GTFSMissingIssue

@pytest.fixture(autouse=True)
def mock_validate_path():
    with patch("src.utils.stations_validation.validate_path", side_effect=lambda p, n: p):
        yield

def test_detects_unmapped_gtfs_stops(tmp_path):
    stations = [
        {
            "name": "Station A",
            "vor_id": "100",
            "aliases": ["100"],
            "latitude": 48.0,
            "longitude": 16.0
        }
    ]
    stations_path = tmp_path / "stations.json"
    stations_path.write_text(json.dumps({"stations": stations}))

    gtfs_path = tmp_path / "stops.txt"
    gtfs_path.write_text("stop_id,stop_name\n100,Station A\n200,Station B\n")

    report = validate_stations(stations_path, gtfs_stops_path=gtfs_path)

    assert len(report.gtfs_missing_issues) == 1
    issue = report.gtfs_missing_issues[0]
    assert isinstance(issue, GTFSMissingIssue)
    assert issue.stop_id == "200"
    assert report.has_issues

def test_detects_unmapped_gtfs_stops_with_numeric_aliases(tmp_path):
    stations = [
        {
            "name": "Station A",
            "vor_id": "100", # primary ID
            "aliases": ["100", "101"], # 101 is an alias which is also a GTFS ID
            "latitude": 48.0,
            "longitude": 16.0
        }
    ]
    stations_path = tmp_path / "stations.json"
    stations_path.write_text(json.dumps({"stations": stations}))

    gtfs_path = tmp_path / "stops.txt"
    gtfs_path.write_text("stop_id,stop_name\n100,Station A\n101,Station A Alias\n200,Station B\n")

    report = validate_stations(stations_path, gtfs_stops_path=gtfs_path)

    # 100 is found via vor_id
    # 101 is found via numeric alias
    # 200 is missing
    assert len(report.gtfs_missing_issues) == 1
    assert report.gtfs_missing_issues[0].stop_id == "200"
