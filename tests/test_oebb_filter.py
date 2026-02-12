
import pytest
from unittest.mock import patch, MagicMock
from providers.oebb import _is_relevant, _load_station_sets, _VIENNA_STATIONS_RE, _VIENNA_STATIONS, _OUTER_STATIONS, _PENDLER_STATIONS

@pytest.fixture(autouse=True)
def reload_stations():
    # Force reload of station sets before each test to ensure fresh state
    import providers.oebb as oebb
    oebb._VIENNA_STATIONS = None
    oebb._OUTER_STATIONS = None
    oebb._PENDLER_STATIONS = None
    oebb._VIENNA_STATIONS_RE = None
    oebb._OUTER_STATIONS_RE = None
    oebb._PENDLER_STATIONS_RE = None
    _load_station_sets()

def test_sigmundsherberg_hadersdorf_kamp_filtered():
    """
    Verify that the route 'Sigmundsherberg ↔ Hadersdorf/Kamp' is filtered out.
    Neither endpoint is in Vienna, and 'Hadersdorf/Kamp' should not match 'Wien Hadersdorf'.
    """
    title = "Sigmundsherberg ↔ Hadersdorf/Kamp"
    description = "Wegen Bauarbeiten können zwischen Sigmundsherberg Bahnhof und Hadersdorf/Kamp Bahnhof..."

    assert _is_relevant(title, description) is False, "Should be filtered out (irrelevant to Vienna)"

def test_wien_hadersdorf_included():
    """
    Verify that 'Wien Hadersdorf' is still recognized as a Vienna station.
    """
    title = "Störung in Wien Hadersdorf"
    description = "Verspätungen..."

    assert _is_relevant(title, description) is True, "Should be relevant (Wien Hadersdorf)"

def test_hadersdorf_ambiguity():
    """
    Verify that just 'Hadersdorf' is NOT sufficient to trigger relevance if it was removed from aliases,
    UNLESS it matches some other pattern.
    Note: If 'Hadersdorf' was removed, searching for 'Hadersdorf' alone should return False
    if no other context is provided.
    However, if 'Bahnhof Hadersdorf' matches, it might return True.
    """
    # 'Hadersdorf' alone might be ambiguous.
    # With the fix, 'Hadersdorf' alone should NOT match 'Wien Hadersdorf' alias.
    # But let's check if 'Bahnhof Hadersdorf' is an alias.

    # Text with just "Hadersdorf"
    assert _is_relevant("Störung in Hadersdorf", "") is False, "Hadersdorf alone should not match if removed from aliases"

    # Text with "Wien Hadersdorf"
    assert _is_relevant("Störung in Wien Hadersdorf", "") is True

def test_pendler_route_filtered_if_no_vienna():
    """
    Verify that a route between two Outer/Pendler stations is filtered if no Vienna context.
    e.g. 'Bad Vöslau ↔ Baden' (both Pendler).
    """
    # Bad Vöslau and Baden are Pendler stations.
    # They should be filtered out if 'Wien' is not mentioned.
    title = "Bad Vöslau ↔ Baden"
    description = "..."
    assert _is_relevant(title, description) is False
