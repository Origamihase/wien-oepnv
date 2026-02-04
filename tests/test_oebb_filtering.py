import pytest
from providers.oebb import _is_relevant

class TestOebbFiltering:
    """
    Tests for the strict filtering logic in the ÖBB provider.
    Ensures that irrelevant messages (far from Vienna) are excluded.
    """

    def test_irrelevant_routes_excluded(self):
        # Case 1: Route between unknown foreign/remote stations (Ljubljana -> Schwarzach)
        # Should be excluded because neither station is known, and title has arrow (Route Heuristic).
        assert _is_relevant(
            "Ljubljana ↔ Schwarzach im Pongau-St.Veit",
            "Wegen Bauarbeiten der Slowenischen Eisenbahnen (SZ) werden von 24.01.2026 bis 25.01.2026 ..."
        ) is False

    def test_irrelevant_generic_title_with_remote_stations(self):
        # Case 2: Generic title with arrow, stations in description (Innsbruck -> Feldkirch)
        # "Innsbruck Hbf" should NOT match "Hbf" (generic alias excluded).
        assert _is_relevant(
            "Bauarbeiten ↔ Umleitung/Haltausfall/Schienenersatzverkehr/geänderte Fahrzeiten",
            "Wegen Bauarbeiten werden zwischen Innsbruck Hbf und Feldkirch Bahnhof von 07.01.2026 ..."
        ) is False

    def test_irrelevant_badly_formatted_title(self):
        # Case 3: Badly formatted title with extra angle brackets (Villach -> Jesenice)
        # "Villach Hbf" should NOT match "Hbf".
        assert _is_relevant(
            "Villach < ↔ > Jesenice(SL)",
            "Wegen Bauarbeiten der Slowenischen Eisenbahnen (SZ) können zwischen Villach Hbf und Jesenice(SL)..."
        ) is False

    def test_irrelevant_vorarlberg_route(self):
        # Case 4: Bregenz -> Dornbirn (Vorarlberg)
        assert _is_relevant(
            "Bregenz < ↔ > Dornbirn",
            "Wegen Bauarbeiten fährt zwischen Bregenz Bahnhof und Dornbirn Bahnhof..."
        ) is False

    def test_relevant_vienna_explicit(self):
        # Explicit Vienna mention
        assert _is_relevant(
            "Wien Hauptbahnhof ↔ St. Pölten",
            "Zugausfall wegen technischer Störung."
        ) is True

    def test_relevant_vienna_alias(self):
        # "Meidling" is a known alias for "Wien Meidling"
        assert _is_relevant(
            "Meidling ↔ Mödling",
            "Verzögerungen."
        ) is True

    def test_relevant_general_disruption(self):
        # General disruption without station or arrow
        # Strict Mode: Excluded unless explicitly mentioning Vienna.
        assert _is_relevant(
            "Sturmwarnung",
            "Es kommt zu Verzögerungen im gesamten Netz."
        ) is False

    def test_relevant_general_disruption_with_vienna(self):
        # General disruption WITH Vienna reference -> Keep
        assert _is_relevant(
            "Sturm im Raum Wien",
            "Verzögerungen bei der S-Bahn Wien."
        ) is True

    def test_irrelevant_outer_only(self):
        # Route strictly within Outer region (e.g. Baden -> Wr. Neustadt)
        # Should be excluded by Check C (Ausschluss Umland)
        # Assuming Baden and Wr. Neustadt are in Outer set.
        assert _is_relevant(
            "Baden ↔ Wiener Neustadt Hbf",
            "Zugausfall."
        ) is False

    def test_hbf_alias_exclusion(self):
        # Ensure "Hbf" alone does not trigger Vienna relevance
        # "Hbf" is in excluded generic aliases.
        # "Innsbruck Hbf" -> should be excluded (unless Innsbruck is in Vienna/Outer, which it isn't).
        # And since it has no arrow, it might be KEPT by fallback if we are not careful?
        # Wait, if "Innsbruck Hbf" is the title, no arrow.
        # Check A: False.
        # Check B: "Hbf" excluded -> False.
        # Check C: False.
        # Check D: No arrow -> True (Fallback).
        # This is expected behavior for non-route messages (we prefer to keep if unsure).
        # BUT if it is a ROUTE "Innsbruck Hbf ↔ Something", arrow is present -> Excluded.
        assert _is_relevant(
            "Innsbruck Hbf ↔ Salzburg Hbf",
            "Verspätung."
        ) is False

    def test_marchegg_bratislava_excluded(self):
        # Regression test for user report: "Marchegg ↔ Bratislava hl.st."
        # Marchegg is in Outer region (pendler=True).
        # Bratislava is foreign (not Vienna).
        # Should be excluded by Check C (Marchegg matches Outer, no Vienna match)
        # or Check D (Route heuristic).
        assert _is_relevant(
            "Marchegg ↔ Bratislava hl.st.",
            "Wegen Bauarbeiten können zwischen Marchegg Bahnhof und Bratislava hl.st. von 04.05.2026 (07:50 Uhr) bis 08.05.2026 (16:00 Uhr) keine REX8-Züge …[04.05.2026 – 08.05.2026]"
        ) is False

    def test_irrelevant_st_margrethen_sg(self):
        # Regression test for "Lindau (Bodensee) Reutin ↔ ST. MARGRETHEN SG"
        # "SG" (St. Gallen) used to match "Sg" (alias for Wien Grillgasse).
        # We now filter out 2-letter aliases to prevent this.
        assert _is_relevant(
            "Lindau (Bodensee) Reutin ↔ ST. MARGRETHEN SG",
            "Wegen Bauarbeiten der Deutschen Bahn (DB) können zwischen Lindau (Bodensee) Reutin Bahnhof und ST. MARGRETHEN SG..."
        ) is False

class TestSigmundsherbergRegression:
    """
    Regression test for Sigmundsherberg ↔ Hadersdorf am Kamp.
    This route should be excluded because it is far from Vienna.
    Previously, 'Hadersdorf am Kamp' triggered a false positive for 'Wien Hadersdorf'.
    """

    def test_sigmundsherberg_hadersdorf_excluded(self):
        title = "Sigmundsherberg ↔ Hadersdorf am Kamp"
        description = "Wegen Bauarbeiten können zwischen Sigmundsherberg Bahnhof und Hadersdorf am Kamp am 16.07., 20.08., 17.09., 15.10. und 19.11.2026 einige …"

        # Should be False (excluded)
        assert _is_relevant(title, description) is False
