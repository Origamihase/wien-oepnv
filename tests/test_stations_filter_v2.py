
from src.utils.stations import (
    text_has_vienna_connection,
    get_stations_in_text,
    _station_name_mapping
)

class TestStationsFilterV2:
    """
    Tests for the new exact station matching and filtering logic.
    """

    def test_explicit_vienna_station(self):
        # "Wien Hauptbahnhof" is a Vienna station -> True
        text = "Störung in Wien Hauptbahnhof wegen Bauarbeiten."
        assert text_has_vienna_connection(text) is True

    def test_commuter_station_no_vienna(self):
        # "St. Pölten Hbf" is a commuter/outer station, but not in Vienna.
        # If no other Vienna reference exists -> False
        text = "Verzögerung in St. Pölten Hbf."
        assert text_has_vienna_connection(text) is False

    def test_commuter_station_with_vienna_word(self):
        # "St. Pölten Hbf" (outer) + "Wien" (word) -> True
        text = "Zug von St. Pölten Hbf nach Wien verspätet."
        assert text_has_vienna_connection(text) is True

    def test_flughafen_wien_false_positive_prevention(self):
        # "Flughafen Wien" is an outer station (pendler=True, in_vienna=False).
        # It contains the word "Wien", but the logic should strip the station name first.
        # So "Flughafen Wien" alone -> False
        text = "Störung am Flughafen Wien."
        assert text_has_vienna_connection(text) is False

    def test_flughafen_wien_with_explicit_vienna_connection(self):
        # "Flughafen Wien" + separate "Wien" word -> True
        text = "Verbindung Flughafen Wien nach Wien Mitte unterbrochen."
        # "Wien Mitte" is a Vienna station -> True
        assert text_has_vienna_connection(text) is True

    def test_flughafen_wien_with_generic_vienna_word(self):
        # "Flughafen Wien" + "Wien" (direction) -> True
        text = "Zug von Flughafen Wien Richtung Wien fällt aus."
        assert text_has_vienna_connection(text) is True

    def test_wien_standalone(self):
        # No station, just "Wien" -> True
        text = "Großstörung im Raum Wien."
        assert text_has_vienna_connection(text) is True

    def test_no_stations_no_wien(self):
        # Irrelevant text -> False
        text = "Baustelle in Linz."
        assert text_has_vienna_connection(text) is False

    def test_boundary_check_wienerschnitzel(self):
        # "Wienerschnitzel" contains "Wien" but is not "Wien" -> False
        text = "Heute gibt es Wienerschnitzel."
        assert text_has_vienna_connection(text) is False

    def test_boundary_check_vienna(self):
        # "Vienna" -> True
        assert text_has_vienna_connection("Vienna calling") is True
        # "Viennale" -> False
        assert text_has_vienna_connection("Viennale Filmfestival") is False

    def test_station_matcher_finds_stations(self):
        text = "Wir fahren von Wien Westbahnhof nach St. Pölten Hbf."
        stations = get_stations_in_text(text)
        names = [s["name"] for s in stations]

        # Should find both (canonical names might differ slightly)
        # "Wien Westbahnhof" maps to "Wien Westbf"
        # "St. Pölten Hbf" maps to "St.Pölten Hbf"
        assert "Wien Westbf" in names
        assert "St.Pölten Hbf" in names

    def test_station_mapping_excludes_generic(self):
        mapping = _station_name_mapping()
        # Ensure generic terms are NOT in the mapping keys
        assert "bahnhof" not in mapping
        assert "hbf" not in mapping
        assert "wien" not in mapping  # "Wien" is handled by the regex fallback, not as a station name
        assert "vienna" not in mapping

    def test_station_mapping_includes_valid(self):
        mapping = _station_name_mapping()
        assert "wien mitte" in mapping
        assert "meidling" in mapping # Alias for Wien Meidling

    def test_short_alias_exclusion(self):
        # Ensure very short aliases (len < 3) are excluded unless digits
        mapping = _station_name_mapping()
        # Assuming "Ka" or similar might exist as alias, should be filtered if < 3
        for key in mapping:
            if not key.isdigit():
                assert len(key) >= 3

    def test_numeric_station_id(self):
        # Ensure numeric IDs are not filtered out if they are valid aliases?
        # The logic allows digits: `if len(n) < 3 or n.isdigit() ... continue`
        # Wait, the logic says: `if len(n) < 3 or n.isdigit() ... continue`
        # So numeric aliases ARE filtered out in the mapping creation!
        # This prevents "123" in text matching a station ID.

        mapping = _station_name_mapping()
        for key in mapping:
            assert not key.isdigit()

    def test_complex_sentence(self):
        text = "Wegen Bauarbeiten in Bruck/Leitha und Himberg kommt es zu Verzögerungen Richtung Wien."
        # Bruck/Leitha -> Outer
        # Himberg -> Outer
        # Wien -> Trigger
        assert text_has_vienna_connection(text) is True

    def test_complex_sentence_irrelevant(self):
        text = "Wegen Bauarbeiten in Bruck/Leitha und Himberg kommt es zu Verzögerungen Richtung Graz."
        # No Vienna connection
        assert text_has_vienna_connection(text) is False

    def test_innsbruck_westbahnhof_filtered(self):
        # "Innsbruck Westbahnhof" should NOT be matched as "Wien Westbahnhof"
        # The new masking logic should handle this.
        assert text_has_vienna_connection("Innsbruck Westbahnhof") is False

    def test_salzburg_hbf_filtered(self):
        # "Salzburg Hbf" should NOT be matched as a Vienna station
        assert text_has_vienna_connection("Salzburg Hbf") is False
