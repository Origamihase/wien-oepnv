from __future__ import annotations

from src.utils import stations as station_utils


def test_point_inside_vienna_polygon() -> None:
    # Stephansdom in the city centre
    assert station_utils.is_in_vienna(48.20849, 16.37208)


def test_point_outside_vienna_polygon() -> None:
    # Linz Hauptbahnhof
    assert not station_utils.is_in_vienna(48.30694, 14.28583)


def test_is_in_vienna_handles_invalid_coordinates() -> None:
    assert not station_utils.is_in_vienna(None, None)
