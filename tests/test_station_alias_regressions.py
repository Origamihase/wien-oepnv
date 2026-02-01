"""Regression tests that ensure station aliases are preserved across updates."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.update_station_directory import (
    Station,
    _load_existing_station_entries,
    _restore_existing_metadata,
)


def _load_station_payload(path: Path) -> list[dict[str, object]]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, dict):
        payload = payload.get("stations", [])
    if not isinstance(payload, list):
        pytest.fail("stations.json must contain a list of station entries")
    return [entry for entry in payload if isinstance(entry, dict)]


def test_restore_existing_metadata_rehydrates_full_station_entries() -> None:
    """Ensure that restoring metadata reproduces the original JSON entries."""

    stations_path = Path("data/stations.json")
    payload = _load_station_payload(stations_path)
    existing_entries = _load_existing_station_entries(stations_path)

    stations: list[Station] = []
    for entry in payload:
        bst_id = entry.get("bst_id")
        bst_code = entry.get("bst_code")
        name = entry.get("name")
        if not isinstance(bst_id, int) or not isinstance(bst_code, str) or not isinstance(name, str):
            continue

        station = Station(
            bst_id=bst_id,
            bst_code=bst_code,
            name=name,
            in_vienna=bool(entry.get("in_vienna", False)),
            pendler=bool(entry.get("pendler", False)),
        )
        # Simulate an update run where supplemental metadata is missing before restoration.
        station.vor_id = None
        stations.append(station)

    _restore_existing_metadata(stations, existing_entries)

    for station in stations:
        restored = station.as_dict()
        original = existing_entries.get(station.bst_id)
        assert original is not None
        assert restored == original


def test_restore_existing_metadata_preserves_all_aliases() -> None:
    """No station should lose aliases during metadata restoration."""

    stations_path = Path("data/stations.json")
    payload = _load_station_payload(stations_path)
    existing_entries = _load_existing_station_entries(stations_path)

    stations: list[Station] = []
    for entry in payload:
        bst_id = entry.get("bst_id")
        bst_code = entry.get("bst_code")
        name = entry.get("name")
        if not isinstance(bst_id, int) or not isinstance(bst_code, str) or not isinstance(name, str):
            continue

        stations.append(
            Station(
                bst_id=bst_id,
                bst_code=bst_code,
                name=name,
                in_vienna=bool(entry.get("in_vienna", False)),
                pendler=bool(entry.get("pendler", False)),
            )
        )

    _restore_existing_metadata(stations, existing_entries)

    for station in stations:
        restored_aliases = station.as_dict().get("aliases")
        original_aliases = existing_entries[station.bst_id].get("aliases")

        if original_aliases is None:
            assert restored_aliases is None
            continue

        assert isinstance(restored_aliases, list), "Restored aliases must be a list"
        assert set(restored_aliases) == set(original_aliases), f"Aliases changed for {station.name}"

