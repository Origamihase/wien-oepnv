"""Regression tests for :mod:`scripts.update_wl_stations`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import update_wl_stations


@pytest.fixture()
def stations_path(tmp_path: Path) -> Path:
    path = tmp_path / "stations.json"
    path.write_text("[]", encoding="utf-8")
    return path


def _read_entries(path: Path) -> list[dict[str, object]]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_merge_wl_data_into_existing_vor_entry(stations_path: Path) -> None:
    stations_path.write_text(
        json.dumps(
            [
                {
                    "name": "Wien Karlsplatz",
                    "vor_id": "490065700",
                    "bst_id": "900101",
                    "aliases": ["Wien Karlsplatz"],
                    "source": "vor",
                }
            ]
        ),
        encoding="utf-8",
    )

    wl_entries = [
        {
            "name": "Wien Karlsplatz (WL)",
            "vor_id": "490065700",
            "aliases": ["Karlsplatz", "Wien Karlsplatz"],
            "wl_diva": "60201076",
            "wl_stops": [
                {
                    "stop_id": "60201076",
                    "name": "Karlsplatz U (Richtung Reumannplatz)",
                }
            ],
            "source": "wl",
        }
    ]

    update_wl_stations.merge_into_stations(stations_path, wl_entries)

    merged = _read_entries(stations_path)
    assert len(merged) == 1
    entry = merged[0]
    assert entry["source"] == "vor, wl"
    assert entry["wl_diva"] == "60201076"
    assert entry["wl_stops"] == wl_entries[0]["wl_stops"]
    assert set(entry["aliases"]) == {"Karlsplatz", "Wien Karlsplatz"}

    update_wl_stations.merge_into_stations(stations_path, wl_entries)
    rerun = _read_entries(stations_path)
    assert rerun == merged


def test_unmatched_wl_entry_is_appended(stations_path: Path) -> None:
    wl_entries = [
        {
            "name": "Wien Neue Station (WL)",
            "aliases": ["Neue Station"],
            "wl_diva": "60209999",
            "wl_stops": [],
            "source": "wl",
        }
    ]

    update_wl_stations.merge_into_stations(stations_path, wl_entries)

    merged = _read_entries(stations_path)
    assert len(merged) == 1
    entry = merged[0]
    assert entry["source"] == "wl"
    assert entry["wl_diva"] == "60209999"
    assert entry["aliases"] == ["Neue Station"]
