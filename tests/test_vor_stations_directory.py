"""Integration tests for VOR entries in stations.json."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.utils.stations import station_info, vor_station_ids


def test_vor_lookup_by_id():
    info = station_info("900100")
    assert info is not None
    assert info.name == "Wien Aspern Nord"
    assert info.vor_id == "490091000"
    assert info.in_vienna is True
    assert info.latitude == pytest.approx(48.234567)
    assert info.longitude == pytest.approx(16.520123)


def test_vor_lookup_by_alias():
    info = station_info("Vienna Airport")
    assert info is not None
    assert info.name == "Flughafen Wien"
    assert info.vor_id == "430470800"
    assert info.in_vienna is False
    assert info.latitude == pytest.approx(48.120027)
    assert info.longitude == pytest.approx(16.561749)


def test_vor_alias_with_municipality_prefix():
    info = station_info("Schwechat Flughafen Wien Bahnhof")
    assert info is not None
    assert info.name == "Flughafen Wien"
    assert info.vor_id == "430470800"


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

    monkeypatch.setattr(vor, "vor_station_ids", lambda: ("430470800", "490091000"))
    monkeypatch.setattr(vor, "DEFAULT_STATION_ID_FILE", Path("/nonexistent"), raising=False)

    ids = vor._load_station_ids_default()
    assert ids == ["430470800", "490091000"]


def test_vor_entries_have_bst_id_and_code():
    with Path("data/stations.json").open(encoding="utf-8") as handle:
        stations = json.load(handle)

    vor_entries = [entry for entry in stations if entry.get("source") == "vor"]
    assert vor_entries, "expected VOR-sourced station entries"

    for entry in vor_entries:
        assert "bst_id" in entry and entry["bst_id"], f"missing bst_id for {entry['name']}"
        assert "bst_code" in entry and entry["bst_code"], f"missing bst_code for {entry['name']}"


def test_wl_aliases_take_precedence_over_vor_text_aliases():
    info = station_info("Wien Karlsplatz U")
    assert info is not None
    assert info.name == "Wien Karlsplatz (WL)"

    numeric = station_info("490065700")
    assert numeric is not None
    assert numeric.name == "Wien Karlsplatz U (VOR)"

    vor_label = station_info("Wien Karlsplatz U (VOR)")
    assert vor_label is not None
    assert vor_label.name == "Wien Karlsplatz U (VOR)"
