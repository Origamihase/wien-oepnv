import json
from pathlib import Path

import pytest

import src.providers.oebb as oebb
from src.utils import stations as station_utils

_STATIONS_PATH = Path(__file__).resolve().parents[1] / "data" / "stations.json"


@pytest.fixture(scope="module")
def station_entries():
    with _STATIONS_PATH.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        pytest.fail("stations.json must contain a list of station entries")
    entries = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if isinstance(name, str) and name.strip():
            entries.append(entry)
    if not entries:
        pytest.fail("stations.json does not contain any valid station entries")
    return entries


@pytest.fixture(scope="module")
def pendler_station(station_entries):
    for entry in station_entries:
        if entry.get("pendler") and not entry.get("in_vienna"):
            return entry["name"]
    pytest.fail("No pendler station outside Vienna found in stations.json")


@pytest.fixture(scope="module")
def vienna_station(station_entries):
    for entry in station_entries:
        if entry.get("in_vienna"):
            return entry["name"]
    pytest.fail("No Vienna station found in stations.json")


def test_station_flags_match_utils(pendler_station, vienna_station):
    assert station_utils.is_pendler(pendler_station)
    assert not station_utils.is_station_in_vienna(pendler_station)
    assert station_utils.is_station_in_vienna(vienna_station)


@pytest.mark.parametrize("arrow", ["↔", "<->", "->", "—", "–", "→"])
def test_pendler_station_is_whitelisted(arrow: str, pendler_station, vienna_station) -> None:
    assert oebb._keep_by_region(f"{vienna_station} {arrow} {pendler_station}", "")


def test_vienna_station_is_whitelisted(vienna_station):
    assert oebb._keep_by_region(f"{vienna_station} ↔ {vienna_station}", "")


def test_only_vienna_env(monkeypatch, pendler_station, vienna_station):
    monkeypatch.setattr(oebb, "OEBB_ONLY_VIENNA", True)
    assert not oebb._keep_by_region(f"{vienna_station} ↔ {pendler_station}", "")
    assert oebb._keep_by_region(f"{vienna_station} ↔ {vienna_station}", "")
