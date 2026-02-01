"""Ensure station coordinates match Vienna classification flags."""

from __future__ import annotations

import json
from pathlib import Path

from src.utils import stations as station_utils


STATIONS_PATH = Path("data/stations.json")


def _load_station_entries() -> list[dict]:
    with STATIONS_PATH.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
        if isinstance(data, dict):
            return data.get("stations", [])
        return data


def test_coordinates_match_in_vienna_flag() -> None:
    for entry in _load_station_entries():
        lat = entry.get("latitude") or entry.get("lat")
        lon = entry.get("longitude") or entry.get("lon")
        if lat is None or lon is None:
            continue
        computed = station_utils.is_in_vienna(lat, lon)
        assert (
            computed == bool(entry.get("in_vienna"))
        ), f"{entry['name']} has mismatching in_vienna classification"


def test_pendler_entries_always_outside_vienna() -> None:
    for entry in _load_station_entries():
        if not entry.get("pendler"):
            continue
        lat = entry.get("latitude") or entry.get("lat")
        lon = entry.get("longitude") or entry.get("lon")
        if lat is None or lon is None:
            continue
        assert (
            station_utils.is_in_vienna(lat, lon) is False
        ), f"Pendler station {entry['name']} must lie outside Vienna"
