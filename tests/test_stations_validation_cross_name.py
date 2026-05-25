from __future__ import annotations

import json
from pathlib import Path

from src.utils.stations_validation import validate_stations

REPO_ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, entries: list[dict[str, object]]) -> Path:
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def test_distant_alias_matching_other_station_name_is_flagged(tmp_path: Path) -> None:
    # "Grinzing" sits ~12 km from "Karlsplatz" yet carries a "Karlsplatz"
    # alias — the exact contamination shape the write-time guard drops.
    path = _write(tmp_path / "stations.json", [
        {"name": "Karlsplatz", "aliases": ["Karlsplatz"], "latitude": 48.20, "longitude": 16.37},
        {"name": "Grinzing", "aliases": ["Grinzing", "Karlsplatz"], "latitude": 48.30, "longitude": 16.50},
    ])

    report = validate_stations(path)

    assert len(report.cross_name_alias_issues) == 1
    issue = report.cross_name_alias_issues[0]
    assert issue.name == "Grinzing"
    assert issue.label == "Karlsplatz"
    assert issue.label_kind == "alias"
    assert issue.colliding_name == "Karlsplatz"
    assert issue.distance_m > 2000


def test_distant_wl_stop_name_is_flagged(tmp_path: Path) -> None:
    path = _write(tmp_path / "stations.json", [
        {"name": "Karlsplatz", "aliases": ["Karlsplatz"], "latitude": 48.20, "longitude": 16.37},
        {
            "name": "Grinzing",
            "aliases": ["Grinzing"],
            "latitude": 48.30,
            "longitude": 16.50,
            "wl_stops": [{"stop_id": "1", "name": "Karlsplatz", "latitude": 48.30, "longitude": 16.50}],
        },
    ])

    report = validate_stations(path)

    assert len(report.cross_name_alias_issues) == 1
    assert report.cross_name_alias_issues[0].label_kind == "wl_stop"


def test_distant_full_form_alias_is_flagged(tmp_path: Path) -> None:
    # Belt-and-suspenders: the contaminating label is the *full* "Wien X"
    # form, not the short colloquial one. The conservative write-time guard
    # leaves this in place; the validator bares both sides and catches it.
    path = _write(tmp_path / "stations.json", [
        {"name": "Karlsplatz", "aliases": ["Karlsplatz"], "latitude": 48.20, "longitude": 16.37},
        {"name": "Grinzing", "aliases": ["Grinzing", "Wien Karlsplatz"], "latitude": 48.30, "longitude": 16.50},
    ])

    report = validate_stations(path)

    assert len(report.cross_name_alias_issues) == 1
    issue = report.cross_name_alias_issues[0]
    assert issue.name == "Grinzing"
    assert issue.label == "Wien Karlsplatz"
    assert issue.colliding_name == "Karlsplatz"


def test_nearby_same_name_alias_is_not_flagged(tmp_path: Path) -> None:
    # ~0.6 km apart — a legitimate interchange label, kept.
    path = _write(tmp_path / "stations.json", [
        {"name": "Karlsplatz", "aliases": ["Karlsplatz"], "latitude": 48.200, "longitude": 16.370},
        {"name": "Resselgasse", "aliases": ["Resselgasse", "Karlsplatz"], "latitude": 48.205, "longitude": 16.375},
    ])

    report = validate_stations(path)

    assert report.cross_name_alias_issues == ()


def test_own_name_alias_is_not_flagged(tmp_path: Path) -> None:
    path = _write(tmp_path / "stations.json", [
        {"name": "Karlsplatz", "aliases": ["Karlsplatz", "Wien Karlsplatz"], "latitude": 48.20, "longitude": 16.37},
    ])

    report = validate_stations(path)

    assert report.cross_name_alias_issues == ()


def test_wien_prefix_and_provider_suffix_are_stripped(tmp_path: Path) -> None:
    # The canonical name carries the "Wien " prefix and a "(WL)" suffix;
    # the bare-name comparison must still match the plain "Karlsplatz"
    # alias on the distant station.
    path = _write(tmp_path / "stations.json", [
        {"name": "Wien Karlsplatz (WL)", "aliases": ["Wien Karlsplatz (WL)"], "latitude": 48.20, "longitude": 16.37},
        {"name": "Grinzing", "aliases": ["Grinzing", "Karlsplatz"], "latitude": 48.30, "longitude": 16.50},
    ])

    report = validate_stations(path)

    assert len(report.cross_name_alias_issues) == 1
    assert report.cross_name_alias_issues[0].colliding_name == "Wien Karlsplatz (WL)"


def test_cross_name_issue_renders_in_markdown(tmp_path: Path) -> None:
    path = _write(tmp_path / "stations.json", [
        {"name": "Karlsplatz", "aliases": ["Karlsplatz"], "latitude": 48.20, "longitude": 16.37},
        {"name": "Grinzing", "aliases": ["Grinzing", "Karlsplatz"], "latitude": 48.30, "longitude": 16.50},
    ])

    markdown = validate_stations(path).to_markdown()

    assert "*Namens-Alias-Kollisionen*: 1" in markdown
    assert "## Namens-Alias-Kollisionen" in markdown
    assert "Karlsplatz" in markdown


def test_real_directory_has_no_cross_name_alias_issues() -> None:
    # Regression sentinel: the live directory is clean after the
    # write-time guard. Any future contamination >2 km re-trips this.
    stations_path = REPO_ROOT / "data" / "stations.json"
    report = validate_stations(stations_path)
    assert report.cross_name_alias_issues == (), [
        (i.name, i.label, i.colliding_name, i.distance_m)
        for i in report.cross_name_alias_issues
    ]
