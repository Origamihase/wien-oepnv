"""Tests for the Austrian-source coordinate consensus (WL → HAFAS → OSM).

Covers the pure policy resolver (:mod:`src.places.coordinate_consensus`)
and the overlap reconciliation pass wired into
:mod:`scripts.update_wl_stations`. Both are exercised without network: the
resolver is pure, and the reconciliation pass takes injected HAFAS / OSM
lookups.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import FrozenInstanceError

import pytest

from scripts import update_wl_stations
from src.places.coordinate_consensus import (
    CoordinateDecision,
    resolve_at_coordinate,
)

# A WL anchor in central Vienna plus offsets with known great-circle gaps
# (≈111 m per 0.001° of latitude):
_WL = (48.2000, 16.3700)
_HAFAS_NEAR = (48.2009, 16.3700)  # ~100 m from _WL  → agreement
_HAFAS_FAR = (48.2030, 16.3700)  # ~334 m from _WL  → disagreement
_OSM_BY_HAFAS = (48.2028, 16.3700)  # ~22 m from _HAFAS_FAR, ~311 m from _WL
_OSM_BY_WL = (48.2002, 16.3700)  # ~22 m from _WL, ~311 m from _HAFAS_FAR
_OSM_FAR = (48.2100, 16.3700)  # >500 m from both candidates


# --------------------------------------------------------------------------
# resolve_at_coordinate — the pure policy.
# --------------------------------------------------------------------------


def test_wl_and_hafas_agree_keeps_wl() -> None:
    decision = resolve_at_coordinate(wl=_WL, hafas=_HAFAS_NEAR)
    assert decision.decision == "wl_hafas_agree"
    assert decision.chosen_source == "wl"
    assert (decision.latitude, decision.longitude) == _WL
    assert decision.sources == ("wl", "hafas")


def test_missing_hafas_is_wl_only() -> None:
    decision = resolve_at_coordinate(wl=_WL, hafas=None)
    assert decision.decision == "wl_only"
    assert (decision.latitude, decision.longitude) == _WL
    assert decision.sources == ("wl",)


def test_disagreement_osm_endorses_hafas() -> None:
    decision = resolve_at_coordinate(wl=_WL, hafas=_HAFAS_FAR, osm=_OSM_BY_HAFAS)
    assert decision.decision == "osm_picked_hafas"
    assert decision.chosen_source == "hafas"
    assert (decision.latitude, decision.longitude) == _HAFAS_FAR
    assert decision.sources == ("wl", "hafas", "osm")


def test_disagreement_osm_endorses_wl() -> None:
    decision = resolve_at_coordinate(wl=_WL, hafas=_HAFAS_FAR, osm=_OSM_BY_WL)
    assert decision.decision == "osm_picked_wl"
    assert (decision.latitude, decision.longitude) == _WL
    assert decision.sources == ("wl", "hafas", "osm")


def test_disagreement_without_osm_keeps_wl() -> None:
    decision = resolve_at_coordinate(wl=_WL, hafas=_HAFAS_FAR, osm=None)
    assert decision.decision == "unresolved_kept_wl"
    assert (decision.latitude, decision.longitude) == _WL
    assert decision.sources == ("wl", "hafas")


def test_disagreement_osm_far_from_both_is_unresolved() -> None:
    decision = resolve_at_coordinate(wl=_WL, hafas=_HAFAS_FAR, osm=_OSM_FAR)
    assert decision.decision == "unresolved_kept_wl"
    assert (decision.latitude, decision.longitude) == _WL


def test_tolerance_boundary_is_inclusive() -> None:
    from src.utils.geo import calculate_distance_meters

    gap = calculate_distance_meters(_WL[0], _WL[1], _HAFAS_FAR[0], _HAFAS_FAR[1])
    # Exactly at the gap the inclusive ``<=`` must count as agreement …
    at_boundary = resolve_at_coordinate(
        wl=_WL, hafas=_HAFAS_FAR, agree_tolerance_m=gap
    )
    assert at_boundary.decision == "wl_hafas_agree"
    # … and a hair below it flips to a disagreement.
    below = resolve_at_coordinate(
        wl=_WL, hafas=_HAFAS_FAR, agree_tolerance_m=gap - 0.01
    )
    assert below.decision != "wl_hafas_agree"


def test_invalid_wl_raises() -> None:
    with pytest.raises(ValueError):
        resolve_at_coordinate(wl=(91.0, 16.37), hafas=_HAFAS_NEAR)


def test_non_finite_hafas_treated_as_absent() -> None:
    decision = resolve_at_coordinate(wl=_WL, hafas=(float("nan"), 16.37))
    assert decision.decision == "wl_only"


# --------------------------------------------------------------------------
# _reconcile_at_overlap — the wired pass over merged entries.
# --------------------------------------------------------------------------


class _FakeHafas:
    """Records every queried name and replies from a fixed table."""

    def __init__(self, table: Mapping[str, tuple[float, float] | None]) -> None:
        self._table = table
        self.queried: list[str] = []

    def __call__(self, name: str) -> tuple[float, float] | None:
        self.queried.append(name)
        return self._table.get(name)


class _FakeOsm:
    """Lazy OSM index loader that counts how many times it is invoked."""

    def __init__(self, index: Mapping[str, tuple[float, float]]) -> None:
        self._index = index
        self.calls = 0

    def __call__(self) -> Mapping[str, tuple[float, float]]:
        self.calls += 1
        return self._index


def _entry(**kwargs: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "Wien Test",
        "latitude": 48.2500,
        "longitude": 16.4000,
        "source": "oebb,oebb_geonetz",
    }
    base.update(kwargs)
    return base


def test_overlap_agreement_promotes_wl_coordinate() -> None:
    entry = _entry(
        name="Wien Hbf", wl_diva="60200096", hafas_extId="1290401", source="oebb,wl"
    )
    hafas = _FakeHafas({"Wien Hbf": _HAFAS_NEAR})
    osm = _FakeOsm({})

    update_wl_stations._reconcile_at_overlap(
        [entry],
        {"60200096": _WL},
        hafas_lookup=hafas,
        osm_index_loader=osm,
    )

    assert (entry["latitude"], entry["longitude"]) == _WL
    assert "hafas" in str(entry["source"]).split(",")
    assert osm.calls == 0  # agreement never needs the arbiter


def test_overlap_disagreement_osm_picks_hafas() -> None:
    entry = _entry(
        name="Wien Floridsdorf", wl_diva="60200999", eva_nr="8100test", source="oebb,wl"
    )
    hafas = _FakeHafas({"Wien Floridsdorf": _HAFAS_FAR})
    osm = _FakeOsm({update_wl_stations._normalize_key("Wien Floridsdorf"): _OSM_BY_HAFAS})

    update_wl_stations._reconcile_at_overlap(
        [entry],
        {"60200999": _WL},
        hafas_lookup=hafas,
        osm_index_loader=osm,
    )

    assert (entry["latitude"], entry["longitude"]) == _HAFAS_FAR
    assert set(str(entry["source"]).split(",")) >= {"hafas", "osm", "wl"}
    assert osm.calls == 1


def test_missing_hafas_leaves_coordinate_untouched() -> None:
    entry = _entry(
        name="Wien Mitte", wl_diva="60200111", hafas_extId="x", source="oebb,wl"
    )
    before = (entry["latitude"], entry["longitude"], entry["source"])
    hafas = _FakeHafas({"Wien Mitte": None})  # transient outage / not in HAFAS
    osm = _FakeOsm({})

    update_wl_stations._reconcile_at_overlap(
        [entry],
        {"60200111": _WL},
        hafas_lookup=hafas,
        osm_index_loader=osm,
    )

    assert (entry["latitude"], entry["longitude"], entry["source"]) == before
    assert osm.calls == 0


def test_non_overlap_entry_is_never_queried() -> None:
    # wl_diva present but no HAFAS identity → not part of the overlap.
    entry = _entry(name="Stephansplatz", wl_diva="60200222", source="wl")
    before = (entry["latitude"], entry["longitude"])
    hafas = _FakeHafas({"Stephansplatz": _HAFAS_FAR})
    osm = _FakeOsm({})

    update_wl_stations._reconcile_at_overlap(
        [entry],
        {"60200222": _WL},
        hafas_lookup=hafas,
        osm_index_loader=osm,
    )

    assert (entry["latitude"], entry["longitude"]) == before
    assert hafas.queried == []
    assert osm.calls == 0


def test_osm_loader_invoked_once_for_multiple_conflicts() -> None:
    entries = [
        _entry(name="Wien A", wl_diva="1", hafas_extId="a", source="oebb,wl"),
        _entry(name="Wien B", wl_diva="2", hafas_extId="b", source="oebb,wl"),
    ]
    hafas = _FakeHafas({"Wien A": _HAFAS_FAR, "Wien B": _HAFAS_FAR})
    osm = _FakeOsm(
        {
            update_wl_stations._normalize_key("Wien A"): _OSM_BY_WL,
            update_wl_stations._normalize_key("Wien B"): _OSM_BY_HAFAS,
        }
    )

    update_wl_stations._reconcile_at_overlap(
        entries,
        {"1": _WL, "2": _WL},
        hafas_lookup=hafas,
        osm_index_loader=osm,
    )

    assert osm.calls == 1
    assert (entries[0]["latitude"], entries[0]["longitude"]) == _WL
    assert (entries[1]["latitude"], entries[1]["longitude"]) == _HAFAS_FAR


def test_wl_coord_index_skips_invalid_and_dedupes() -> None:
    index = update_wl_stations._wl_coord_index(
        [
            {"wl_diva": "1", "latitude": 48.2, "longitude": 16.37},
            {"wl_diva": "1", "latitude": 48.9, "longitude": 16.0},  # dup → ignored
            {"wl_diva": "2", "latitude": 999.0, "longitude": 16.0},  # invalid
            {"wl_diva": "", "latitude": 48.2, "longitude": 16.0},  # no diva
        ]
    )
    assert index == {"1": (48.2, 16.37)}


def test_reconcile_skips_entry_without_known_wl_coordinate() -> None:
    entry = _entry(name="Wien Ghost", wl_diva="absent", hafas_extId="z")
    before = (entry["latitude"], entry["longitude"])
    hafas = _FakeHafas({"Wien Ghost": _HAFAS_NEAR})
    osm = _FakeOsm({})

    update_wl_stations._reconcile_at_overlap(
        [entry],
        {},  # no WL coordinate for this diva
        hafas_lookup=hafas,
        osm_index_loader=osm,
    )

    assert (entry["latitude"], entry["longitude"]) == before
    assert hafas.queried == []


def test_coordinate_decision_is_frozen() -> None:
    decision = CoordinateDecision(48.2, 16.37, "wl", "wl_only", ("wl",))
    with pytest.raises(FrozenInstanceError):
        decision.latitude = 0.0  # type: ignore[misc]
