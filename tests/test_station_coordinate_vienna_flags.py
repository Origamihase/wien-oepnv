"""Ensure station coordinates match Vienna classification flags."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from src.utils import stations as station_utils


STATIONS_PATH = Path("data/stations.json")


def _load_station_entries() -> list[dict[str, Any]]:
    with STATIONS_PATH.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
        if isinstance(data, dict):
            return cast(list[dict[str, Any]], data.get("stations", []))
        return cast(list[dict[str, Any]], data)


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


def test_polygon_includes_liesing_authoritative_coords() -> None:
    """Wien Liesing's authoritative VOR coordinates must resolve as in-Vienna.

    The previous 8-vertex convex-hull polygon (computed from station
    coordinates) had the Liesing station as a literal vertex, so the more
    precise VOR coordinates ``(48.134853, 16.284229)`` fell just outside.
    The detailed boundary polygon must include them.
    """
    assert station_utils.is_in_vienna(48.134853, 16.284229) is True


def test_polygon_excludes_close_pendler_stations() -> None:
    """The boundary must distinguish Wien from immediate-neighbour pendler hubs.

    These four stations sit within a few hundred meters of the Wien border
    and are reliable canaries: if a polygon simplification accidentally
    over-includes a neighbour district the test catches it.
    """
    # Klosterneuburg-Weidling NW (border with Wien-Döbling)
    assert station_utils.is_in_vienna(48.297585, 16.334586) is False
    # Perchtoldsdorf SW (near Wien-Liesing)
    assert station_utils.is_in_vienna(48.123023, 16.285559) is False
    # Brunn am Gebirge (Mödling district)
    assert station_utils.is_in_vienna(48.105090, 16.288094) is False
    # Kledering SE (Schwechat district)
    assert station_utils.is_in_vienna(48.132453, 16.439724) is False
