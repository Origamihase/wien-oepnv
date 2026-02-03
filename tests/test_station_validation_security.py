from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.utils.stations_validation import validate_stations, CoordinateIssue

def test_validation_rejects_nan_and_infinity(tmp_path: Path) -> None:
    stations = [
        {
            "bst_id": 1,
            "bst_code": "NaN",
            "name": "Station NaN",
            "latitude": "NaN",
            "longitude": 16.1,
            "aliases": ["Station NaN"],
        },
        {
            "bst_id": 2,
            "bst_code": "Inf",
            "name": "Station Inf",
            "latitude": 48.1,
            "longitude": "Infinity",
            "aliases": ["Station Inf"],
        },
        {
            "bst_id": 3,
            "bst_code": "NegInf",
            "name": "Station NegInf",
            "latitude": "-Infinity",
            "longitude": 16.1,
            "aliases": ["Station NegInf"],
        }
    ]
    path = tmp_path / "stations.json"
    path.write_text(json.dumps(stations), encoding="utf-8")

    report = validate_stations(path)

    # We expect these to be treated as missing coordinates because _extract_float should return None
    expected_issues = {
        CoordinateIssue(
            identifier="bst:1 / code:NaN",
            name="Station NaN",
            reason="missing latitude"
        ),
        CoordinateIssue(
            identifier="bst:2 / code:Inf",
            name="Station Inf",
            reason="missing longitude"
        ),
        CoordinateIssue(
            identifier="bst:3 / code:NegInf",
            name="Station NegInf",
            reason="missing latitude"
        )
    }

    assert set(report.coordinate_issues) == expected_issues
