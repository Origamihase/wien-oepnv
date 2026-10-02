"""A stretch that lies on another stretch of the same ÖBB message is not repeated.

Published 2026-09-30 to 2026-10-02 on the info displays, 125 characters::

    R 40/REX 41/REX 4/S 40: Wien Franz-Josefs-Bahnhof ↔ St.Andrä-Wördern /
    Tulln an der Donau / Wien Heiligenstadt / Wien Nußdorf

Every stretch of the four lines starts at Wien Franz-Josefs-Bahnhof and
ends on the way to Tulln an der Donau, so the title is that one stretch.
"""

from __future__ import annotations

from src.providers import oebb
from src.utils.stations import station_info, station_lines


def test_the_franz_josefs_bahn_stretches_are_one() -> None:
    routes = [
        ("Wien Franz-Josefs-Bahnhof", "St.Andrä-Wördern"),
        ("Tulln/Donau", "Wien Franz-Josefs-Bahnhof"),
        ("Wien Franz-Josefs-Bahnhof", "Wien Heiligenstadt"),
        ("Wien Franz-Josefs-Bahnhof", "Wien Nußdorf"),
    ]
    assert oebb._format_route_title(routes, "R 40/REX 41/REX 4/S 40") == (
        "R 40/REX 41/REX 4/S 40: Wien Franz-Josefs-Bahnhof ↔ Tulln an der Donau"
    )


def test_the_suedbahn_stretches_are_one() -> None:
    routes = [("Wien Hbf", "Mödling"), ("Wien Hbf", "Baden"), ("Wien Hbf", "Wiener Neustadt")]
    assert oebb._format_route_title(routes) == "Wien Hauptbahnhof ↔ Wiener Neustadt Hauptbahnhof"


def test_a_station_of_another_line_in_the_same_direction_stays() -> None:
    # Flughafen Wien lies straight between Wien Hauptbahnhof and Bruck an
    # der Leitha, but no line serves all three.
    assert oebb._lies_on_route(
        station_info("Flughafen Wien"), station_info("Wien Hbf"), station_info("Bruck an der Leitha")
    ) is False
    title = oebb._format_route_title([("Wien Hbf", "Flughafen Wien"), ("Wien Hbf", "Bruck an der Leitha")])
    assert "Flughafen Wien" in title
    assert "Bruck an der Leitha" in title


def test_a_station_in_the_other_direction_stays() -> None:
    title = oebb._format_route_title([("Wien Meidling", "Mödling"), ("Wien Meidling", "Wien Floridsdorf")])
    assert title == "Wien Floridsdorf ↔ Wien Meidling ↔ Mödling"


def test_unknown_stations_are_never_merged() -> None:
    assert oebb._lies_on_route(None, station_info("Wien Hbf"), station_info("Mödling")) is False
    title = oebb._format_route_title([("Wien Hbf", "Irgendwo"), ("Wien Hbf", "Mödling")])
    assert "Irgendwo" in title
    assert "Mödling" in title


def test_station_lines_come_from_the_line_directory() -> None:
    assert "S40" in station_lines("Wien Nußdorf")
    assert station_lines("Irgendwo") == frozenset()
