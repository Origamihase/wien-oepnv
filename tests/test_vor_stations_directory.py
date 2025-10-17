"""Integration tests for VOR entries in stations.json."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.utils.stations import station_info, vor_station_ids


def test_vor_lookup_by_id():
    info = station_info("900100")
    assert info is not None
    assert info.name == "Wien Aspern Nord"
    assert info.vor_id == "900100"
    assert info.in_vienna is True
    assert info.latitude == pytest.approx(48.234567)
    assert info.longitude == pytest.approx(16.520123)


def test_vor_lookup_by_alias():
    info = station_info("Vienna Airport")
    assert info is not None
    assert info.name == "Flughafen Wien"
    assert info.vor_id == "900200"
    assert info.in_vienna is False
    assert info.latitude == pytest.approx(48.120027)
    assert info.longitude == pytest.approx(16.561749)


def test_vor_alias_with_municipality_prefix():
    info = station_info("Schwechat Flughafen Wien Bahnhof")
    assert info is not None
    assert info.name == "Flughafen Wien"
    assert info.vor_id == "900200"


def test_vor_does_not_override_station_directory():
    info = station_info("Wiener Neustadt Hbf")
    assert info is not None
    assert info.vor_id == "900300"
    assert info.name == "Wiener Neustadt Hbf"


def test_vor_station_ids_only_cover_vienna_or_pendler():
    ids = vor_station_ids()
    assert ids, "expected VOR station ids"

    for vor_id in ids:
        info = station_info(vor_id)
        assert info is not None, f"missing station info for {vor_id}"
        assert info.in_vienna or info.pendler, f"unexpected non-pendler VOR id {vor_id}"


def test_vor_station_ids_default_prefers_directory(monkeypatch):
    import src.providers.vor as vor

    monkeypatch.setattr(vor, "vor_station_ids", lambda: ("900100", "900200"))
    monkeypatch.setattr(vor, "DEFAULT_STATION_ID_FILE", Path("/nonexistent"), raising=False)

    ids = vor._load_station_ids_default()
    assert ids == ["900100", "900200"]
