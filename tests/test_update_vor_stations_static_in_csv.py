"""Regression test for the STATIC_VOR_ENTRIES + CSV-vor_id race condition.

Before this fix, ``update_vor_stations.merge_into_stations`` silently
dropped any STATIC entry whose ``vor_id`` was also present in the
fresh VOR CSV but had no matching existing station. The static-merge
loop at the end of the function skipped such ids because their vor_id
was already in ``seen_vor_ids`` (added at the top of the main loop),
and the main loop skipped them because no existing target was found
(``vor_id_to_entry`` had no match).

Concrete impact: ``Guntramsdorf Bahnhof`` (vor_id 430361600) was added
to STATIC_VOR_ENTRIES in PR #1207 specifically to close a Top-12 gap,
but it disappeared from ``stations.json`` on every cron run — the VOR
CSV contained the resolved stop, ÖBB Excel had no "Guntramsdorf" row,
and the bug above silently swallowed the entry.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import update_vor_stations


def test_static_entry_creates_new_directory_entry_when_csv_has_vor_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    static_template = {
        "vor_id": "430361600",
        "name": "Guntramsdorf Bahnhof",
        "in_vienna": False,
        "pendler": True,
        "latitude": 48.051964,
        "longitude": 16.297551,
        "aliases": [
            "Guntramsdorf Bahnhof",
            "Guntramsdorf",
            "Guntramsdorf Südbahn",
            "430361600",
        ],
        "bst_id": "430361600",
        "bst_code": "430361600",
        "source": "vor",
    }

    monkeypatch.setattr(
        update_vor_stations,
        "STATIC_VOR_ENTRIES",
        (static_template,),
    )

    # Existing stations: nothing matching vor_id 430361600 by bst_id
    stations_path = tmp_path / "stations.json"
    stations_path.write_text(
        json.dumps(
            {
                "stations": [
                    {
                        "name": "Wien Hauptbahnhof",
                        "bst_id": "900100",
                        "vor_id": "490134900",
                        "in_vienna": True,
                        "pendler": False,
                        "source": "oebb",
                        "aliases": ["Wien Hauptbahnhof"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    # vor_entries simulates the fresh CSV containing the same vor_id
    # as the STATIC template — the exact race condition.
    vor_entries: list[dict[str, object]] = [
        {
            "vor_id": "430361600",
            "name": "Guntramsdorf Bahnhof",
            "latitude": 48.051964,
            "longitude": 16.297551,
            "aliases": ["Guntramsdorf Bahnhof"],
        }
    ]

    update_vor_stations.merge_into_stations(stations_path, vor_entries)

    payload = json.loads(stations_path.read_text(encoding="utf-8"))
    names = {entry["name"] for entry in payload["stations"]}
    assert "Guntramsdorf Bahnhof" in names, (
        "STATIC entry must be created as a new directory entry when its "
        "vor_id is in the CSV but no existing station matches by bst_id"
    )

    guntramsdorf = next(
        e for e in payload["stations"] if e["name"] == "Guntramsdorf Bahnhof"
    )
    assert guntramsdorf["vor_id"] == "430361600"
    assert guntramsdorf["pendler"] is True
    assert guntramsdorf["in_vienna"] is False
    assert guntramsdorf["latitude"] == pytest.approx(48.051964)
    assert guntramsdorf["longitude"] == pytest.approx(16.297551)
    assert "Guntramsdorf Südbahn" in guntramsdorf["aliases"]


def test_static_entry_merges_when_existing_station_matches_by_bst_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Don't regress the existing path: a STATIC template whose vor_id
    matches an existing station by bst_id must merge into that entry,
    not duplicate it. This was the existing behaviour before the bug
    fix and must stay intact."""
    static_template = {
        "vor_id": "490134900",
        "name": "Wien Hauptbahnhof (VOR)",
        "in_vienna": True,
        "pendler": False,
        "latitude": 48.185184,
        "longitude": 16.376413,
        "aliases": ["Wien Hauptbahnhof", "490134900"],
        "bst_id": "900100",
        "bst_code": "900100",
        "source": "vor",
    }
    monkeypatch.setattr(
        update_vor_stations,
        "STATIC_VOR_ENTRIES",
        (static_template,),
    )

    stations_path = tmp_path / "stations.json"
    stations_path.write_text(
        json.dumps(
            {
                "stations": [
                    {
                        "name": "Wien Hauptbahnhof",
                        "bst_id": "900100",
                        "vor_id": "490134900",
                        "in_vienna": True,
                        "pendler": False,
                        "source": "oebb",
                        "aliases": ["Wien Hauptbahnhof"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    vor_entries: list[dict[str, object]] = [
        {
            "vor_id": "490134900",
            "name": "Wien Hauptbahnhof",
            "latitude": 48.185184,
            "longitude": 16.376413,
            "aliases": ["Wien Hauptbahnhof"],
        }
    ]

    update_vor_stations.merge_into_stations(stations_path, vor_entries)

    payload = json.loads(stations_path.read_text(encoding="utf-8"))
    # Exactly one Wien Hauptbahnhof entry — merge, not duplicate
    matches = [
        e for e in payload["stations"] if "Wien Hauptbahnhof" in e["name"]
    ]
    assert len(matches) == 1, (
        "STATIC template must merge into existing entry by bst_id, "
        "not create a duplicate"
    )
