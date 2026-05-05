"""Tests for the name→vor_id direct mapping integration in update_station_directory.

When ``data/vor-haltestellen.mapping.json`` (produced by
fetch_vor_haltestellen) is present, ``_assign_vor_ids`` short-circuits
the fuzzy matcher and uses the explicit name→vor_id mapping. This
solves the empty-vor_id problem the 2026-05 cron exposed for
Hohenau/Götzendorf/Hennersdorf etc., where the resolved VOR name
("Hohenau an der March Bahnhof") was too dissimilar from the ÖBB
station name ("Hohenau") for the existing fuzzy index to match.
"""
from __future__ import annotations

import json
from pathlib import Path

from scripts import update_station_directory as usd


def _make_station(name: str, bst_id: str = "1") -> usd.Station:
    return usd.Station(bst_id=bst_id, bst_code="X", name=name, in_vienna=False, pendler=False)


def test_load_vor_name_to_id_map_handles_missing_file(tmp_path: Path) -> None:
    """Absent file degrades gracefully — fuzzy matcher remains primary."""
    assert usd._load_vor_name_to_id_map(tmp_path / "nope.json") == {}


def test_load_vor_name_to_id_map_parses_mapping(tmp_path: Path) -> None:
    path = tmp_path / "vor-haltestellen.mapping.json"
    payload = [
        {"station_name": "Hohenau", "vor_id": "430377800",
         "resolved_name": "Hohenau an der March Bahnhof"},
        {"station_name": "Götzendorf", "vor_id": "430365500",
         "resolved_name": "Götzendorf/Leitha Bahnhof"},
        # Garbage entries should not crash the loader
        "not a dict",
        {"station_name": "no_vor_id"},
        {"vor_id": "no_name"},
    ]
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = usd._load_vor_name_to_id_map(path)
    assert result == {
        "Hohenau": "430377800",
        "Götzendorf": "430365500",
    }


def test_assign_vor_ids_uses_direct_mapping_for_disambiguated_resolves() -> None:
    """The 2026-05 cron failed because ``_select_vor_stop`` couldn't
    pick a clear winner when the resolved name carried a heavy suffix.
    The direct name→vor_id map sidesteps that ambiguity."""
    station = _make_station("Hohenau", bst_id="1543")
    assert station.vor_id is None

    name_map = {"Hohenau": "430377800"}
    usd._assign_vor_ids([station], vor_stops=[], name_to_vor_id=name_map)

    assert station.vor_id == "430377800"


def test_assign_vor_ids_skips_already_assigned() -> None:
    """Stations that already have a vor_id are not re-assigned even if
    a different mapping exists in the file (defensive: the existing
    metadata is presumed authoritative)."""
    station = _make_station("Hohenau", bst_id="1543")
    station.vor_id = "EXISTING"
    name_map = {"Hohenau": "430377800"}
    usd._assign_vor_ids([station], vor_stops=[], name_to_vor_id=name_map)
    assert station.vor_id == "EXISTING"


def test_assign_vor_ids_falls_back_to_fuzzy_matcher_when_name_not_in_map() -> None:
    """If a station's name isn't in the direct map, the existing fuzzy
    index/select_vor_stop path keeps working."""
    station = _make_station("Wien Aspern Nord", bst_id="4773541")
    vor_stops = [
        usd.VORStop(vor_id="490091000", name="Wien Aspern Nord", municipality="Wien"),
    ]
    usd._assign_vor_ids([station], vor_stops, name_to_vor_id={})  # empty map
    assert station.vor_id == "490091000"


def test_assign_vor_ids_refuses_duplicate_assignment() -> None:
    """Two different stations resolving to the same VOR id must not both
    receive that id — the second assignment is refused with a warning.
    Regression for the 2026-05 cron Mistelbach-collision: 'Mistelbach'
    and 'Mistelbach Stadt' both got mapped to vor_id 430420200, tripping
    the cross_station_id_issues gate."""
    station_a = _make_station("Mistelbach", bst_id="1370")
    station_b = _make_station("Mistelbach Stadt", bst_id="1945177")
    name_map = {
        "Mistelbach": "430420200",
        "Mistelbach Stadt": "430420200",  # same vor_id — must not be claimed twice
    }

    usd._assign_vor_ids([station_a, station_b], vor_stops=[], name_to_vor_id=name_map)

    assert station_a.vor_id == "430420200"
    assert station_b.vor_id is None, (
        "second station must not claim the already-assigned vor_id"
    )


def test_assign_vor_ids_respects_pre_existing_vor_ids() -> None:
    """If a station already has a vor_id at function entry, that id is
    treated as claimed for the duplicate-guard check."""
    station_a = _make_station("Existing", bst_id="1")
    station_a.vor_id = "430420200"
    station_b = _make_station("New", bst_id="2")
    name_map = {"New": "430420200"}

    usd._assign_vor_ids([station_a, station_b], vor_stops=[], name_to_vor_id=name_map)

    assert station_a.vor_id == "430420200"
    assert station_b.vor_id is None
