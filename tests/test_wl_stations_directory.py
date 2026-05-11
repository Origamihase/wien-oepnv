"""Integration tests for Wiener Linien entries in stations.json."""
from __future__ import annotations

from typing import Any

from src.utils.stations import StationInfo, station_info, canonical_name


# Any: returns element of info.wl_stops; element type not imported here
def _stop(info: StationInfo, stop_id: str) -> Any:
    for stop in info.wl_stops:
        if stop.stop_id == stop_id:
            return stop
    raise AssertionError(f"Stop {stop_id} not found in {info.wl_stops!r}")


def test_wl_stop_lookup_by_stop_id() -> None:
    # The canonical wienerlinien.at OGD-Echtzeit schema renumbered the
    # legacy data.wien.gv.at DIVAs (see PR #1444): ``60200657`` is
    # Wien Karlsplatz's current haltestelle DIVA (the pre-2026-05
    # value ``60201076`` is now Ratzenhofergasse).
    info = station_info("60200657")
    assert info is not None
    assert info.name == "Wien Karlsplatz"
    assert info.wl_diva == "60200657"
    assert info.in_vienna is True
    assert any(stop.latitude is not None for stop in info.wl_stops)


def test_wl_alias_matching_by_name() -> None:
    info = station_info("Schottentor U")
    assert info is not None
    assert info.name == "Wien Schottentor"
    # Post-PR #1444 renumbering: Schottentor's haltestelle DIVA is now
    # ``60201184`` (was ``60201002`` in the legacy data.wien.gv.at
    # schema, which is Pensionsversicherungsanstalt in the new CSV).
    assert info.wl_diva == "60201184"
    assert len(info.wl_stops) >= 1


def test_wl_canonical_name_for_diva() -> None:
    assert canonical_name("Stephansplatz U") == "Wien Stephansplatz"
