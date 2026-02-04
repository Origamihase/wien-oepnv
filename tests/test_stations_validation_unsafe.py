import json
import pytest
from pathlib import Path
from src.utils.stations_validation import validate_stations, SecurityIssue

def test_validation_flags_unsafe_chars(tmp_path):
    stations_file = tmp_path / "stations.json"
    data = {
        "stations": [
            {
                "name": "Normal Station",
                "latitude": 48.0,
                "longitude": 16.0,
                "aliases": ["Normal Station", "Safe Alias"]
            },
            {
                "name": "Hacked <script>alert(1)</script>",
                "latitude": 48.1,
                "longitude": 16.1,
                "aliases": ["Hacked <script>alert(1)</script>", "Another <bad> alias"],
                "bst_code": "Evil\x00Code"
            }
        ]
    }
    stations_file.write_text(json.dumps(data), encoding="utf-8")

    report = validate_stations(stations_file, decimal_places=2)

    assert report.has_issues
    # We expect security issues. There might be other issues if I messed up the required fields for aliases logic,
    # but let's focus on security issues.

    reasons = [issue.reason for issue in report.security_issues]
    assert any("Unsafe characters in name" in r for r in reasons)
    assert any("Unsafe characters in alias" in r for r in reasons)
    assert any("Unsafe characters in bst_code" in r for r in reasons)

def test_validation_passes_safe_chars(tmp_path):
    stations_file = tmp_path / "stations.json"
    data = {
        "stations": [
            {
                "name": "Safe Station (Hbf)",
                "latitude": 48.0,
                "longitude": 16.0,
                "aliases": ["Safe Station (Hbf)", "Safe / Alias", "St. Pölten"]
            }
        ]
    }
    stations_file.write_text(json.dumps(data), encoding="utf-8")

    report = validate_stations(stations_file, decimal_places=2)

    # Debugging if issues exist
    if report.has_issues:
        print("Unexpected issues:", report)

    assert not report.has_issues
    assert len(report.security_issues) == 0
