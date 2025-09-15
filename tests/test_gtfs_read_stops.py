from __future__ import annotations

from pathlib import Path

import pytest

from scripts.gtfs import DEFAULT_GTFS_STOP_PATH, GTFSStop, read_gtfs_stops


def test_read_gtfs_stops_reads_sample_file():
    stops = read_gtfs_stops()

    assert DEFAULT_GTFS_STOP_PATH.exists()
    assert "8103000" in stops
    assert isinstance(stops["8103000"], GTFSStop)

    station = stops["8103000"]
    assert station.stop_name == "Wien Hbf (Bahnsteige 1-2)"
    assert station.stop_code is None
    assert station.location_type == 1
    assert station.parent_station is None

    platform = stops["8103000:1"]
    assert platform.parent_station == "8103000"
    assert platform.platform_code == "1"
    assert platform.stop_lat == pytest.approx(48.185750)
    assert platform.stop_lon == pytest.approx(16.375120)

    child_without_location = stops["8103001:1"]
    assert child_without_location.location_type is None
    assert child_without_location.platform_code == "1"


def test_read_gtfs_stops_handles_custom_path(tmp_path: Path):
    sample = tmp_path / "stops.txt"
    sample.write_text(
        "stop_id,stop_name,stop_lat,stop_lon,location_type,parent_station,platform_code\n"
        "child,Example,48.0,16.0,0,parent,\n"
        " ,Missing,,,,,\n",
        encoding="utf-8",
    )

    stops = read_gtfs_stops(sample)

    assert list(stops.keys()) == ["child"]
    assert stops["child"].stop_name == "Example"
    assert stops["child"].location_type == 0
    assert stops["child"].parent_station == "parent"
    assert stops["child"].platform_code is None


def test_read_gtfs_stops_requires_stop_id_column(tmp_path: Path):
    sample = tmp_path / "stops.txt"
    sample.write_text("stop_name\nFoo\n", encoding="utf-8")

    with pytest.raises(ValueError):
        read_gtfs_stops(sample)
