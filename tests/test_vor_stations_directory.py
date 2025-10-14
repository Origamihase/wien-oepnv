"""Integration tests for VOR entries in stations.json."""
from __future__ import annotations

import pytest

from src.utils.stations import station_info


def test_vor_lookup_by_id():
    info = station_info("900100")
    assert info is not None
    assert info.name == "Wien Aspern Nord"
    assert info.vor_id == "490091000"
    assert info.in_vienna is True
    assert info.latitude == pytest.approx(48.234669)
    assert info.longitude == pytest.approx(16.504456)


def test_vor_lookup_by_alias():
    info = station_info("Vienna Airport")
    assert info is not None
    assert info.name == "Flughafen Wien"
    assert info.vor_id == "430470800"
    assert info.in_vienna is False
    assert info.latitude == pytest.approx(48.12056)
    assert info.longitude == pytest.approx(16.563659)


def test_vor_alias_with_municipality_prefix():
    info = station_info("Schwechat Flughafen Wien Bahnhof")
    assert info is not None
    assert info.name == "Flughafen Wien"
    assert info.vor_id == "430470800"


def test_vor_does_not_override_station_directory():
    info = station_info("Wiener Neustadt Hbf")
    assert info is not None
    assert info.vor_id == "430521000"
    assert info.name == "Wiener Neustadt Hbf"
