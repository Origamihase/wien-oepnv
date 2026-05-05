
from typing import Any

import pytest

from src.providers.oebb import _is_relevant

def test_venezia_is_excluded() -> None:
    # Per spec: routes between a Wiener Bahnhof and a foreign/unknown
    # destination (Vienna ↔ Distant) are NOT relevant — only Wien-Wien and
    # Wien-Pendler routes belong in the feed.
    title = "Wien Hauptbahnhof ↔ Venezia Santa Lucia"
    description = "Wegen Bauarbeiten..."
    assert _is_relevant(title, description) is False

def test_wien_st_poelten_included() -> None:
    # St. Pölten is in the Pendler list
    title = "Wien Hauptbahnhof ↔ St. Pölten Hbf"
    description = "Verzögerungen..."
    assert _is_relevant(title, description) is True

def test_wien_west_meidling_included() -> None:
    # Both in Vienna
    title = "Wien Westbahnhof ↔ Wien Meidling"
    description = "Technische Störung..."
    assert _is_relevant(title, description) is True

def test_unknown_route_excluded() -> None:
    # Both unknown
    title = "Paris Gare de l'Est ↔ München Hbf"
    description = "Streik..."
    assert _is_relevant(title, description) is False

def test_one_end_unknown_excluded() -> None:
    # Per spec: one endpoint unknown means we cannot verify the connection
    # ends at a Wiener Bahnhof or Pendlerbahnhof — drop it. A loose Wien
    # mention in the body must not override the strict route check.
    title = "Wien Hbf ↔ Unknown City"
    description = "Wien Hauptbahnhof ist betroffen."
    assert _is_relevant(title, description) is False

def test_bauarbeiten_category_included() -> None:
    # Not a route "A ↔ B" but a category "Category: Detail"
    # _is_relevant checks for "↔" in title.
    # If title is "Bauarbeiten: Wien Hbf", no "↔".
    title = "Bauarbeiten: Wien Hbf"
    description = "Wartungsarbeiten..."
    assert _is_relevant(title, description) is True

def test_bauarbeiten_arrow_umleitung_excluded_if_no_station() -> None:
    # "Bauarbeiten ↔ Umleitung"
    # If these are not stations, they return None for station_info.
    # RELAXED: But if "Wien Hbf" is in description, it is RELEVANT.
    title = "Bauarbeiten ↔ Umleitung"
    description = "In Wien Hbf..."
    assert _is_relevant(title, description) is True

def test_flughafen_wien_included() -> None:
    # Flughafen Wien is a pendler station
    title = "Wien Hbf ↔ Flughafen Wien"
    description = "..."
    assert _is_relevant(title, description) is True

def test_rex51_neulengbach_tullnerbach_irrelevant() -> None:
    # REX 51: Neulengbach ↔ Tullnerbach-Pressbaum
    # Should be IRRELEVANT (False).
    # Currently might be True if "51" matches Vienna regex OR if "Neulengbach" is not parsed correctly.
    # After fix, it should be False.
    title = "REX 51: Neulengbach ↔ Tullnerbach-Pressbaum"
    description = "Wegen einer Oberleitungsstörung..."
    assert _is_relevant(title, description) is False

def test_prefix_outer_outer_with_wien_in_title_irrelevant() -> None:
    # If the title has "Wien" inside the prefix (e.g. "REX (Wien): ...")
    # but the stations are outer-outer, it should be irrelevant.
    # _is_relevant checks `text = f"{title} {description}"`.
    # If title contains "Wien", `text_has_vienna_connection` is True.
    # The logic for Outer-Outer exception is:
    # if is_outer0 and is_outer1:
    #    if not text_has_vienna_connection(description): return False
    # Note: it checks DESCRIPTION only!

    # So if title has "Wien" but description doesn't, and stations are outer-outer, it should be False.
    title = "REX 51 (Wien): Neulengbach ↔ Tullnerbach-Pressbaum"
    description = "Oberleitungsstörung."
    assert _is_relevant(title, description) is False

def test_fernverkehr_mit_prefix_negativ() -> None:
    title = "REX 51: Störung: Salzburg Hbf ↔ Linz/Donau Hbf"
    description = ""
    assert _is_relevant(title, description) is False

def test_einseitiger_wien_bezug_mit_prefix() -> None:
    # Per strict spec: Wien ↔ Distant (Budapest-Keleti is foreign, not Pendler)
    # is NOT relevant. The feed targets Wien-Wien and Wien-Pendler routes only.
    title = "REX 51: Störung: Wien Meidling ↔ Budapest-Keleti"
    description = ""
    assert _is_relevant(title, description) is False

def test_wien_bezug_im_zweiten_teil() -> None:
    title = "Störung: Verspätung: Mödling ↔ Wien Hbf"
    description = ""
    assert _is_relevant(title, description) is True

def test_stationsname_enthaelt_selbst_doppelpunkt(monkeypatch: pytest.MonkeyPatch) -> None:
    # Verifies that a station name containing a colon (e.g. "Wien 10.: Favoriten")
    # is preserved through prefix stripping and recognised as Vienna.
    # Paired with a Pendler endpoint (Mödling) so that the strict route filter
    # keeps the message — the focus of this test is the colon handling, not
    # the destination classification.
    title = "RJ 123: Wien 10.: Favoriten ↔ Mödling"
    description = ""

    from src.providers.oebb import station_info
    original_station_info = station_info

    def mock_station_info(name: str) -> Any:
        if name == "Wien 10.: Favoriten":
            from src.utils.stations import StationInfo
            return StationInfo(
                name="Wien 10.: Favoriten", in_vienna=True, pendler=False,
                wl_diva=None, wl_stops=(), vor_id=None, latitude=None, longitude=None, source="mock"
            )
        return original_station_info(name)

    monkeypatch.setattr("src.providers.oebb.station_info", mock_station_info)
    assert _is_relevant(title, description) is True
