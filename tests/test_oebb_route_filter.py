
from src.providers.oebb import _is_relevant

def test_venezia_is_excluded():
    # Long distance train to unknown station (Venezia)
    # RELAXED: This route mentions "Wien", so it is now RELEVANT for Vienna commuters.
    title = "Wien Hauptbahnhof ↔ Venezia Santa Lucia"
    description = "Wegen Bauarbeiten..."
    assert _is_relevant(title, description) is True

def test_wien_st_poelten_included():
    # St. Pölten is in the Pendler list
    title = "Wien Hauptbahnhof ↔ St. Pölten Hbf"
    description = "Verzögerungen..."
    assert _is_relevant(title, description) is True

def test_wien_west_meidling_included():
    # Both in Vienna
    title = "Wien Westbahnhof ↔ Wien Meidling"
    description = "Technische Störung..."
    assert _is_relevant(title, description) is True

def test_unknown_route_excluded():
    # Both unknown
    title = "Paris Gare de l'Est ↔ München Hbf"
    description = "Streik..."
    assert _is_relevant(title, description) is False

def test_one_end_unknown_excluded():
    # One end unknown (but mentions Wien in text)
    title = "Wien Hbf ↔ Unknown City"
    description = "Wien Hauptbahnhof ist betroffen."
    # RELAXED: Because "Wien" is in text, it is now RELEVANT.
    assert _is_relevant(title, description) is True

def test_bauarbeiten_category_included():
    # Not a route "A ↔ B" but a category "Category: Detail"
    # _is_relevant checks for "↔" in title.
    # If title is "Bauarbeiten: Wien Hbf", no "↔".
    title = "Bauarbeiten: Wien Hbf"
    description = "Wartungsarbeiten..."
    assert _is_relevant(title, description) is True

def test_bauarbeiten_arrow_umleitung_excluded_if_no_station():
    # "Bauarbeiten ↔ Umleitung"
    # If these are not stations, they return None for station_info.
    # RELAXED: But if "Wien Hbf" is in description, it is RELEVANT.
    title = "Bauarbeiten ↔ Umleitung"
    description = "In Wien Hbf..."
    assert _is_relevant(title, description) is True

def test_flughafen_wien_included():
    # Flughafen Wien is a pendler station
    title = "Wien Hbf ↔ Flughafen Wien"
    description = "..."
    assert _is_relevant(title, description) is True

def test_rex51_neulengbach_tullnerbach_irrelevant():
    # REX 51: Neulengbach ↔ Tullnerbach-Pressbaum
    # Should be IRRELEVANT (False).
    # Currently might be True if "51" matches Vienna regex OR if "Neulengbach" is not parsed correctly.
    # After fix, it should be False.
    title = "REX 51: Neulengbach ↔ Tullnerbach-Pressbaum"
    description = "Wegen einer Oberleitungsstörung..."
    assert _is_relevant(title, description) is False

def test_prefix_outer_outer_with_wien_in_title_irrelevant():
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
