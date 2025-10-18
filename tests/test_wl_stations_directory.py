"""Integration tests for Wiener Linien entries in stations.json."""
from __future__ import annotations

import pytest

from src.utils.stations import StationInfo, station_info, canonical_name


def _stop(info: StationInfo, stop_id: str):
    for stop in info.wl_stops:
        if stop.stop_id == stop_id:
            return stop
    raise AssertionError(f"Stop {stop_id} not found in {info.wl_stops!r}")


def test_wl_stop_lookup_by_stop_id():
    info = station_info("60201076")
    assert info is not None
    assert info.name == "Wien Karlsplatz"
    assert info.wl_diva == "60201076"
    stop = _stop(info, "60201076")
    assert stop.latitude == pytest.approx(48.19868)
    assert stop.longitude == pytest.approx(16.36945)
    assert info.in_vienna is True
    assert any(s.stop_id == "60201077" for s in info.wl_stops)


def test_wl_alias_matching_by_name():
    info = station_info("Schottentor U (Richtung Karlsplatz)")
    assert info is not None
    assert info.name == "Wien Schottentor"
    assert info.wl_diva == "60201002"
    ids = sorted(stop.stop_id for stop in info.wl_stops)
    assert ids == ["60201002", "60201003"]
    assert any("Heiligenstadt" in (stop.name or "") for stop in info.wl_stops)


def test_wl_canonical_name_for_diva():
    assert canonical_name("Stephansplatz U") == "Wien Stephansplatz"
