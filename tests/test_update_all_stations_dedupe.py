"""Regression tests for the orchestrator's exact-duplicate dedup pass.

Pins the fix for the recurring auto-quarantine of Wien Siebenhirten /
Wien Handelskai. ``scripts/update_station_directory.py`` assembles its
output as ``fresh + manual_stations`` with no dedup pass, so a station
preserved through the existing-file → ``manual_stations`` round-trip can
be emitted twice. Because ``enrich_station_aliases`` intentionally lists a
station's own ``bst_code`` among its aliases, two byte-identical copies
cross-collide (A's alias shadows B's ``bst_code`` and vice versa). The
validator's self-exclusion (``colliding_entry is not entry``) only protects
a *single* copy, so both duplicates were flagged and the auto-quarantine
removed *every* matching entry — dropping a valid station every run.

``scripts.update_all_stations._dedupe_exact_duplicates`` collapses exact
duplicates to one copy before validation, restoring the self-exclusion
invariant.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import update_all_stations  # noqa: E402
from src.utils.stations_validation import (  # noqa: E402
    _find_cross_station_id_conflicts,
)


def _siebenhirten() -> dict[str, Any]:
    """A minimal entry that carries its own ``bst_code`` as an alias."""
    return {
        "bst_id": "1371",
        "bst_code": "Mb  H2H",
        "name": "Siebenhirten",
        "source": "oebb_geonetz,osm",
        "eva_nr": "8101523",
        "aliases": [
            "Siebenhirten",
            "Bahnhof Siebenhirten",
            "Mb  H2H",
            "Siebenhirten Bahnhof",
        ],
    }


def test_dedupe_removes_byte_identical_entry() -> None:
    a, dup_a, b = _siebenhirten(), _siebenhirten(), {"name": "Other", "bst_id": "2"}
    deduped, removed = update_all_stations._dedupe_exact_duplicates([a, dup_a, b])

    assert removed == 1
    assert len(deduped) == 2
    # First-seen order is preserved; the surviving copy is the first one.
    assert deduped[0] is a
    assert deduped[1] is b


def test_dedupe_keeps_distinct_entries() -> None:
    a = _siebenhirten()
    b = _siebenhirten()
    b["eva_nr"] = "9999999"  # differs in one field → genuine conflict, not a dup
    deduped, removed = update_all_stations._dedupe_exact_duplicates([a, b])

    assert removed == 0
    assert len(deduped) == 2


def test_dedupe_collapses_duplicate_and_result_is_collision_free() -> None:
    """The dedup pass collapses byte-identical copies to one clean record.

    The validator's self-collision guard (see
    ``tests/test_stations_validation_self_collision_guard.py``) means two
    same-``bst_code`` copies are no longer mis-flagged as a cross-station
    collision, so neither the pair nor the single copy raises an issue; the
    dedup pass additionally removes the redundant copy so the directory
    never carries two identical records.
    """
    duplicated = [_siebenhirten(), _siebenhirten()]
    # Shared-bst_code self-shadow is guarded → no cross-station issue.
    assert list(_find_cross_station_id_conflicts(duplicated)) == []

    deduped, removed = update_all_stations._dedupe_exact_duplicates(duplicated)
    assert removed == 1
    assert len(deduped) == 1
    assert list(_find_cross_station_id_conflicts(deduped)) == []
