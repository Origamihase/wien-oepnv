"""Zero-trust shape guard for ``_load_vor_mapping``.

The mapping file ``data/vor-haltestellen.mapping.json`` is produced by a
sibling script and consumed by ``enrich_station_aliases.py`` inside the
``update_all_stations.py`` cron pipeline (subprocess.run with check=True).
A corrupted/tampered file that decodes as a non-list JSON value (null,
int, bool, dict) used to crash the loop with TypeError and take the
whole pipeline down — the documented `return {}` fallback was bypassed.

These tests pin the shape guard so the loop never propagates an
exception out of the loader, mirroring the existing fallback path used
for JSON decode failures.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.enrich_station_aliases import _load_vor_mapping


@pytest.mark.parametrize(
    "payload",
    [
        None,
        42,
        True,
        False,
        "a string",
        {"not": "a list"},
    ],
)
def test_load_vor_mapping_returns_empty_for_non_list_payload(
    tmp_path: Path, payload: object
) -> None:
    """Non-list JSON values must not raise — return {} instead."""
    path = tmp_path / "vor-haltestellen.mapping.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert _load_vor_mapping(path) == {}


def test_load_vor_mapping_skips_entries_with_non_string_resolved_name(
    tmp_path: Path,
) -> None:
    """A list entry whose ``resolved_name`` is non-str must be skipped, not
    crash with AttributeError on ``.strip()``. The valid entry alongside
    must still be loaded so loop continuity matches the existing
    failure-handling contract."""
    path = tmp_path / "vor-haltestellen.mapping.json"
    path.write_text(
        json.dumps(
            [
                {"bst_id": 100, "resolved_name": ["unexpected", "list"]},
                {"bst_id": 101, "resolved_name": {"unexpected": "dict"}},
                {"bst_id": 102, "resolved_name": 12345},
                {"bst_id": 103, "resolved_name": "Wien Hauptbahnhof"},
            ]
        ),
        encoding="utf-8",
    )
    assert _load_vor_mapping(path) == {103: "Wien Hauptbahnhof"}


def test_load_vor_mapping_happy_path(tmp_path: Path) -> None:
    """Sanity: well-formed payloads still load."""
    path = tmp_path / "vor-haltestellen.mapping.json"
    path.write_text(
        json.dumps(
            [
                {"bst_id": 1, "resolved_name": "  Wien Mitte  "},
                {"bst_id": "2", "resolved_name": "Floridsdorf"},
            ]
        ),
        encoding="utf-8",
    )
    assert _load_vor_mapping(path) == {1: "Wien Mitte", 2: "Floridsdorf"}
