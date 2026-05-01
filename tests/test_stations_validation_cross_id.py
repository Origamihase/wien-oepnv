import json
from pathlib import Path


from src.utils.stations_validation import validate_stations


def test_cross_station_id_conflict_bst_code(tmp_path: Path) -> None:
    path = tmp_path / "stations.json"
    entries = [
        {
            "name": "Station A",
            "bst_code": "900100",
            "aliases": ["A"],
            "latitude": 48.1,
            "longitude": 16.1,
            "source": "vor",
        },
        {
            "name": "Station B",
            "bst_code": "900200",
            "aliases": ["B", "900100"],
            "latitude": 48.2,
            "longitude": 16.2,
            "source": "vor",
        },
    ]
    path.write_text(json.dumps(entries), encoding="utf-8")

    report = validate_stations(path)
    assert len(report.cross_station_id_issues) == 1
    issue = report.cross_station_id_issues[0]
    assert issue.alias == "900100"
    assert issue.name == "Station B"
    assert issue.colliding_name == "Station A"
    assert issue.colliding_field == "bst_code"


def test_cross_station_id_conflict_vor_id(tmp_path: Path) -> None:
    path = tmp_path / "stations.json"
    entries = [
        {
            "name": "Station A",
            "vor_id": "4900001",
            "aliases": ["A"],
            "latitude": 48.1,
            "longitude": 16.1,
            "source": "vor",
        },
        {
            "name": "Station B",
            "vor_id": "4900002",
            "aliases": ["B", "4900001"],
            "latitude": 48.2,
            "longitude": 16.2,
            "source": "vor",
        },
    ]
    path.write_text(json.dumps(entries), encoding="utf-8")

    report = validate_stations(path)
    assert len(report.cross_station_id_issues) == 1
    issue = report.cross_station_id_issues[0]
    assert issue.alias == "4900001"
    assert issue.name == "Station B"
    assert issue.colliding_name == "Station A"
    assert issue.colliding_field == "vor_id"


def test_cross_station_id_no_conflict_self_reference(tmp_path: Path) -> None:
    path = tmp_path / "stations.json"
    entries = [
        {
            "name": "Station A",
            "bst_id": 100,
            "aliases": ["A", "100"],
            "latitude": 48.1,
            "longitude": 16.1,
            "source": "vor",
        },
    ]
    path.write_text(json.dumps(entries), encoding="utf-8")

    report = validate_stations(path)
    assert not report.cross_station_id_issues


def test_cross_station_id_no_conflict_clean_data(tmp_path: Path) -> None:
    path = tmp_path / "stations.json"
    entries = [
        {
            "name": "Station A",
            "bst_code": "900100",
            "aliases": ["A"],
            "latitude": 48.1,
            "longitude": 16.1,
            "source": "vor",
        },
        {
            "name": "Station B",
            "bst_code": "900200",
            "aliases": ["B"],
            "latitude": 48.2,
            "longitude": 16.2,
            "source": "vor",
        },
    ]
    path.write_text(json.dumps(entries), encoding="utf-8")

    report = validate_stations(path)
    assert not report.cross_station_id_issues


def test_cross_station_id_real_data() -> None:
    path = Path("data/stations.json")
    report = validate_stations(path)
    assert not report.cross_station_id_issues
