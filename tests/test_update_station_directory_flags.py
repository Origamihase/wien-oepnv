"""Mutual-exclusivity guard for in_vienna and pendler flags.

Verifies that ``_annotate_station_flags`` never produces an entry with
both flags set, even if a Vienna station's bst_id is mistakenly listed
in ``data/pendler_bst_ids.json``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from scripts import update_station_directory as usd


def _make_station(name: str, bst_id: str = "1") -> usd.Station:
    return usd.Station(bst_id=bst_id, bst_code="X", name=name, in_vienna=False, pendler=False)


def _make_location(lat: float, lon: float, *, source: str = "oebb") -> usd.LocationInfo:
    return usd.LocationInfo(latitude=lat, longitude=lon, sources={source})


def test_in_vienna_wins_over_mistaken_pendler_whitelist_entry(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A station inside Vienna must keep pendler=False even when its
    bst_id sneaks into pendler_bst_ids.json. The override logs a warning."""
    station = _make_station("Wien Westbahnhof", bst_id="2511")
    # Wien Westbahnhof's actual coordinates from data/stations.json
    locations = {"wien westbahnhof": _make_location(48.196654, 16.337652)}
    pendler_ids = {"2511"}  # mistakenly added Wien station

    with caplog.at_level(logging.WARNING):
        usd._annotate_station_flags([station], pendler_ids, locations)

    assert station.in_vienna is True
    assert station.pendler is False, "Vienna stations must never carry pendler=true"
    assert any("inside Vienna" in record.getMessage() for record in caplog.records), (
        "Expected a WARNING when a Vienna station is on the pendler whitelist"
    )


def test_outside_station_is_marked_pendler_only() -> None:
    """A station outside Vienna with a pendler whitelist entry stays pendler-only."""
    station = _make_station("Mödling", bst_id="1390")
    locations = {"modling": _make_location(48.085628, 16.295474)}
    pendler_ids = {"1390"}

    usd._annotate_station_flags([station], pendler_ids, locations)

    assert station.in_vienna is False
    assert station.pendler is True


def test_wl_outside_station_becomes_pendler() -> None:
    """A WL-sourced station outside Vienna is auto-promoted to pendler=True
    even without a whitelist entry — preserves legacy behaviour."""
    station = _make_station("Eisenstadt Domplatz", bst_id="9999")
    locations = {"eisenstadt domplatz": _make_location(47.846, 16.522, source="wl")}
    pendler_ids: set[str] = set()

    usd._annotate_station_flags([station], pendler_ids, locations)

    assert station.in_vienna is False
    assert station.pendler is True


def test_wl_vienna_station_does_not_become_pendler() -> None:
    """The WL-auto-promotion path must respect the in_vienna check.
    A WL station inside Vienna stays pendler=False."""
    station = _make_station("Wien Karlsplatz", bst_id="900101")
    locations = {"wien karlsplatz": _make_location(48.200888, 16.368907, source="wl")}
    pendler_ids: set[str] = set()

    usd._annotate_station_flags([station], pendler_ids, locations)

    assert station.in_vienna is True
    assert station.pendler is False


def test_extras_coords_override_name_heuristic_when_info_missing() -> None:
    """When no fresh ``LocationInfo`` is available, ``_annotate_station_flags``
    must fall back to the polygon check on extras-stored coords BEFORE the
    name heuristic.

    Regression: a U-Bahn stop like ``Stephansplatz`` carries valid coords in
    ``extras`` (carried forward from the prior run by
    ``_restore_existing_metadata``) but its name does NOT start with "Wien".
    Without this fallback a single failed WL fetch flips ``in_vienna`` to
    False because ``_is_vienna_station("Stephansplatz") == False``.
    """
    station = _make_station("Stephansplatz", bst_id="900900")
    station.extras["latitude"] = 48.20849
    station.extras["longitude"] = 16.37306

    usd._annotate_station_flags([station], pendler_ids=set(), locations={})

    assert station.in_vienna is True, (
        "extras-stored coords inside the Vienna polygon must classify "
        "in_vienna=True even when LocationInfo is missing"
    )
    assert station.pendler is False


def test_extras_coords_keep_outside_station_pendler_eligible() -> None:
    """The extras-coord fallback must also correctly classify outside
    stations as in_vienna=False — otherwise a pendler-belt station with
    extras coords would lose its pendler flag.
    """
    station = _make_station("Mödling", bst_id="1390")
    station.extras["latitude"] = 48.085628
    station.extras["longitude"] = 16.295474

    usd._annotate_station_flags(
        [station], pendler_ids={"1390"}, locations={}
    )

    assert station.in_vienna is False
    assert station.pendler is True


def test_name_heuristic_fires_only_when_no_coords_available() -> None:
    """If extras has no coords AND no LocationInfo is provided, the
    name heuristic remains the last-resort fallback (preserving legacy
    behaviour for unenriched manual entries)."""
    # No extras coords, no LocationInfo — the only signal is the name.
    inside = _make_station("Wien Tunnelbach", bst_id="900901")
    outside = _make_station("Tunnelbach", bst_id="900902")

    usd._annotate_station_flags(
        [inside, outside], pendler_ids=set(), locations={}
    )

    assert inside.in_vienna is True, "name starts with 'Wien' → True"
    assert outside.in_vienna is False, "name does not match → False"


def test_load_existing_treats_legacy_bst_id_entry_without_source_as_oebb(
    tmp_path: Path,
) -> None:
    """Entries written before PR #1203's source-default fix lack a source
    field entirely. The loader must treat them as ÖBB (not as manual)
    when bst_id + bst_code are present, otherwise the next Excel pull
    creates a duplicate and trips the naming-uniqueness gate."""
    import json

    legacy_payload = [
        {
            "bst_id": "100",
            "bst_code": "Aw",
            "name": "St.Andrä-Wördern",
            "in_vienna": False,
            "pendler": True,
            "aliases": ["St.Andrä-Wördern"],
            # No `source` field — characteristic of pre-#1203 entries
        },
        {
            # A genuinely manual entry — no bst_code → still manual
            "name": "Roma Termini",
            "in_vienna": False,
            "pendler": False,
            "aliases": ["Roma Termini"],
            "type": "manual_foreign_city",
        },
    ]
    path = tmp_path / "stations.json"
    path.write_text(json.dumps(legacy_payload), encoding="utf-8")

    by_bst, manual = usd._load_existing_station_entries(path)
    assert "100" in by_bst, (
        "legacy ÖBB entry without source must be loaded by bst_id, not parked as manual"
    )
    assert {entry.get("name") for entry in manual} == {"Roma Termini"}, (
        "true manual entries (no bst_code) stay parked as manual"
    )
