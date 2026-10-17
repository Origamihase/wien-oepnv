"""The station directory carries no OpenStreetMap data (Michael, 17.10.2026).

OSM coordinates are under the ODbL, whose share-alike would cover the whole
published ``data/stations.json``; the file also holds data that cannot be put
under the ODbL. So the weekly run has no OSM tier any more, the WL/HAFAS
reconciliation has no OSM arbiter, and a station without coordinates gets
its GeoNetz point (joined on ``bst_id`` only) before HAFAS is asked.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import update_station_directory as usd
from scripts import update_wl_stations as uws
from scripts.update_station_directory import Station, _fill_coordinates_from_geonetz

REPO_ROOT = Path(__file__).resolve().parent.parent


def _sources(entry: dict[str, object]) -> set[str]:
    return {t.strip() for t in str(entry.get("source") or "").split(",") if t.strip()}


def test_directory_has_no_osm_source() -> None:
    payload = json.loads((REPO_ROOT / "data" / "stations.json").read_text(encoding="utf-8"))
    tagged = [e["name"] for e in payload["stations"] if "osm" in _sources(e)]
    assert tagged == []


def test_station_runs_have_no_osm_tier() -> None:
    assert not hasattr(usd, "_enrich_with_osm")
    assert not hasattr(usd, "fetch_osm_places")
    assert "--osm-enrich" not in usd.parse_args.__code__.co_consts
    assert not hasattr(uws, "_build_osm_index_loader")


def test_parse_args_rejects_osm_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["update_station_directory.py", "--osm-enrich"])
    with pytest.raises(SystemExit):
        usd.parse_args()


def _station(bst_id: str, name: str, **extras: object) -> Station:
    return Station(bst_id=bst_id, bst_code="X", name=name, extras=dict(extras))


def test_fill_uses_geonetz_point_of_the_bst_id() -> None:
    lookup = {"1371": {"bsts_id": "1371", "name": "Siebenhirten", "lat": 48.611421, "lon": 16.531152}}
    station = _station("1371", "Siebenhirten", source="oebb")

    assert _fill_coordinates_from_geonetz([station], lookup) == 1

    assert station.extras["latitude"] == 48.611421
    assert station.extras["longitude"] == 16.531152
    assert station.extras["source"] == "oebb,oebb_geonetz"


def test_fill_never_joins_on_the_name() -> None:
    # A same-named GeoNetz stop under another bst_id is a different place.
    lookup = {"1371": {"bsts_id": "1371", "name": "Siebenhirten", "lat": 48.611421, "lon": 16.531152}}
    station = _station("9999", "Siebenhirten", source="oebb")

    assert _fill_coordinates_from_geonetz([station], lookup) == 0
    assert "latitude" not in station.extras


def test_fill_keeps_existing_coordinates() -> None:
    lookup = {"11": {"bsts_id": "11", "name": "Achau", "lat": 48.0, "lon": 16.0}}
    station = _station("11", "Achau", latitude=48.080882, longitude=16.378004, source="oebb")

    assert _fill_coordinates_from_geonetz([station], lookup) == 0
    assert station.extras["latitude"] == 48.080882


def test_fill_skips_unusable_geonetz_values() -> None:
    lookup: dict[str, dict[str, object]] = {
        "1": {"lat": True, "lon": 16.0},
        "2": {"lat": float("nan"), "lon": 16.0},
        "3": {"lat": 120.0, "lon": 16.0},
        "4": {"lat": "48.1", "lon": "16.3"},
    }
    stations = [_station(str(i), f"S{i}") for i in range(1, 5)]

    assert _fill_coordinates_from_geonetz(stations, lookup) == 0


def test_boundary_check_uses_geonetz_point_not_the_name() -> None:
    # The name-keyed location is the Wiener Linien stop in Liesing; the
    # station with bst_id 1371 lies near Mistelbach.
    liesing = usd.LocationInfo(latitude=48.129676, longitude=16.309845, sources={"wl"})
    lookup = {"1371": {"bsts_id": "1371", "lat": 48.611421, "lon": 16.531152}}
    station = Station(bst_id="1371", bst_code="Mb  H2H", name="Siebenhirten")

    usd._annotate_station_flags(
        [station], set(), {"siebenhirten": liesing}, geonetz_lookup=lookup
    )

    assert station.in_vienna is False
    assert station.pendler is False


def test_one_entry_per_eva_number_keeps_the_known_one() -> None:
    known: dict[str, object] = {"bst_id": "779", "name": "Wien Handelskai", "eva_nr": "8101934"}
    synthetic: dict[str, object] = {"bst_id": "900105", "name": "Wien Kaiserebersdorf", "eva_nr": "8100374"}
    new_twin: dict[str, object] = {"bst_id": "1586", "name": "Handelskai", "eva_nr": "8101934"}
    new_real: dict[str, object] = {"bst_id": "1059", "name": "Kaiserebersdorf", "eva_nr": "8100374"}
    plain: dict[str, object] = {"bst_id": "5", "name": "Ohne EVA"}

    kept = usd._drop_duplicate_eva([new_twin, known, new_real, plain, synthetic], {"779"})

    assert [e["name"] for e in kept] == ["Wien Handelskai", "Ohne EVA", "Wien Kaiserebersdorf"]


def test_directory_has_one_entry_per_eva_number() -> None:
    payload = json.loads((REPO_ROOT / "data" / "stations.json").read_text(encoding="utf-8"))
    evas = [e["eva_nr"] for e in payload["stations"] if e.get("eva_nr")]
    assert len(evas) == len(set(evas))
