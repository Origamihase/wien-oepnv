"""Regression tests for the cross-station-ID self-collision guard.

``enrich_station_aliases`` deliberately lists each station's own
``bst_code`` among its aliases. When two records describe the *same*
physical station and share that ``bst_code`` (a duplicate / shared-code
situation — e.g. an ``oebb_geonetz`` Siebenhirten record alongside a
fresh ``oebb`` one, both ``bst_code "Mb  H2H"``), each copy's own-code
alias "shadows" the other's ``bst_code``. Pre-fix, that fired
``_find_cross_station_id_conflicts`` for *both* copies; the orchestrator's
auto-quarantine then removed every entry sharing the identifier and the
station vanished from ``data/stations.json`` (observed for Wien
Siebenhirten / Wien Handelskai).

The orchestrator dedup (PR #1671) only collapses byte-identical copies;
this guard covers the near-identical (shared-``bst_code``, differing other
fields) variant. The genuine condition — an alias shadowing a *different*
station's identity field — must still fire.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.utils.stations_validation import (  # noqa: E402
    _find_cross_station_id_conflicts,
    _find_identity_field_conflicts,
)


def _siebenhirten(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": "Siebenhirten",
        "bst_code": "Mb  H2H",
        "aliases": ["Siebenhirten", "Mb  H2H"],
    }
    entry.update(overrides)
    return entry


def test_shared_bst_code_with_own_code_alias_is_not_cross_station_collision() -> None:
    # Same physical station, same bst_code, each lists its own code as an
    # alias; differ only in source/eva so the orchestrator's byte-identical
    # dedup would NOT catch them. Must NOT be flagged as a cross-station
    # collision (which would quarantine both copies).
    a = _siebenhirten(eva_nr="8101523", source="oebb_geonetz,osm")
    b = _siebenhirten(eva_nr="8101523", source="oebb")
    assert list(_find_cross_station_id_conflicts([a, b])) == []


def test_shared_bst_code_still_surfaced_as_identity_field_conflict() -> None:
    # The real problem (two stations claiming one bst_code) is still reported
    # — just by the non-blocking identity-field check, not the quarantine path.
    a = _siebenhirten(eva_nr="8101523", source="oebb_geonetz,osm")
    b = _siebenhirten(eva_nr="8101523", source="oebb")
    conflicts = list(_find_identity_field_conflicts([a, b]))
    assert any(c.field == "bst_code" and c.value == "Mb  H2H" for c in conflicts)


def test_genuine_alias_shadow_of_other_station_still_flagged() -> None:
    # B's alias equals a DIFFERENT station A's bst_code while B's own bst_code
    # differs → real lookup ambiguity, must still fire.
    a = {"name": "Station A", "bst_code": "900100", "aliases": ["A"]}
    b = {"name": "Station B", "bst_code": "900200", "aliases": ["B", "900100"]}
    issues = list(_find_cross_station_id_conflicts([a, b]))
    assert len(issues) == 1
    assert issues[0].alias == "900100"
    assert issues[0].colliding_field == "bst_code"
    assert issues[0].name == "Station B"
    assert issues[0].colliding_name == "Station A"
