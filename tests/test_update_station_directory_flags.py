"""Mutual-exclusivity guard for in_vienna and pendler flags.

Verifies that ``_annotate_station_flags`` never produces an entry with
both flags set, even if a Vienna station's bst_id is mistakenly listed
in ``data/pendler_bst_ids.json``.
"""

from __future__ import annotations

import logging

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
