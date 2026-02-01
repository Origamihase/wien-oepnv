"""Regression tests for pendler flags on commuter belt stations."""
from __future__ import annotations

import json
from pathlib import Path


COMMUTER_STATIONS = {
    "Gerasdorf",
    "Kledering",
    "Purkersdorf Sanatorium",
    "Schwechat",
}


def _load_station_directory() -> list[dict[str, object]]:
    stations_path = Path(__file__).resolve().parents[1] / "data" / "stations.json"
    with stations_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
        if isinstance(data, dict):
            return data.get("stations", [])
        return data


def _lookup_station(entries: list[dict[str, object]], name: str) -> dict[str, object]:
    for entry in entries:
        if entry.get("name") == name:
            return entry
    raise AssertionError(f"station '{name}' missing from stations.json")


def test_commuter_belt_stations_marked_as_pendler() -> None:
    stations = _load_station_directory()

    for station_name in COMMUTER_STATIONS:
        entry = _lookup_station(stations, station_name)
        assert entry.get("in_vienna") is False, f"{station_name} must not be in Vienna"
        assert entry.get("pendler") is True, f"{station_name} must be marked as pendler"
