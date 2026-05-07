"""Zero-trust shape guard for ``src.providers.vor._load_station_name_map``.

The mapping file ``data/vor-haltestellen.mapping.json`` is produced by
``scripts/fetch_vor_haltestellen.py`` and consumed by **three** loaders:

* ``scripts/update_station_directory.py:_load_vor_name_to_id_map`` — guarded.
* ``scripts/enrich_station_aliases.py:_load_vor_mapping`` — guarded
  (see ``test_enrich_station_aliases_vor_mapping_shape.py``).
* ``src.providers.vor._load_station_name_map`` — *this* test module.

The third loader runs at *import time*: ``STATION_NAME_MAP =
_load_station_name_map()`` executes unconditionally when
``src.providers.vor`` is imported. Without a shape guard, a corrupted /
hand-edited / tampered mapping file that decodes as a non-list JSON
value (null, int, bool) propagates ``TypeError`` out of the module
import and breaks the entire VOR provider — and every consumer of it,
including the feed-build pipeline.

These tests pin the shape guard so the loader fails closed (returns
``{}``) on every non-list JSON value, mirroring the existing fallback
already exercised for JSON-decode and FileNotFoundError failures.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.providers import vor


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
def test_load_station_name_map_returns_empty_for_non_list_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
) -> None:
    """Non-list JSON values must not raise — return ``{}`` instead.

    Specifically, non-iterable JSON values (null, int, bool) used to
    crash ``for entry in data`` with ``TypeError`` at module import,
    bypassing the documented ``return {}`` fallback.
    """
    mapping_file = tmp_path / "vor-haltestellen.mapping.json"
    mapping_file.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(vor, "MAPPING_FILE", mapping_file)
    assert vor._load_station_name_map() == {}


def test_load_station_name_map_happy_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sanity: well-formed list payloads still load."""
    mapping_file = tmp_path / "vor-haltestellen.mapping.json"
    mapping_file.write_text(
        json.dumps(
            [
                {"station_name": "Wien Hbf", "resolved_name": "Wien Hauptbahnhof"},
                {"station_name": "  Mödling  ", "resolved_name": "Mödling Bahnhof"},
                # ``resolved_name`` falls back to ``station_name`` when missing/blank.
                {"station_name": "Baden"},
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(vor, "MAPPING_FILE", mapping_file)
    assert vor._load_station_name_map() == {
        "Wien Hbf": "Wien Hauptbahnhof",
        "Mödling": "Mödling Bahnhof",
        "Baden": "Baden",
    }


def test_load_station_name_map_skips_non_dict_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A list with non-dict entries must skip the bad rows, not crash."""
    mapping_file = tmp_path / "vor-haltestellen.mapping.json"
    mapping_file.write_text(
        json.dumps(
            [
                None,
                "string-entry",
                42,
                ["nested-list"],
                {"station_name": "Wien Mitte", "resolved_name": "Wien Mitte-Landstraße"},
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(vor, "MAPPING_FILE", mapping_file)
    assert vor._load_station_name_map() == {
        "Wien Mitte": "Wien Mitte-Landstraße",
    }
