from src.providers.oebb import _clean_title_keep_places

def test_title_reordering_vienna_first() -> None:
    # Case 1: Vienna station second -> should swap.
    # "Linz/Donau" canonicalises to "Linz Hbf" via the directory.
    t = "Linz/Donau ↔ Wien Hauptbahnhof"
    assert _clean_title_keep_places(t) == "Wien Hauptbahnhof ↔ Linz Hbf"

def test_title_reordering_vienna_first_salzburg() -> None:
    # Case 2: Vienna station second; "Salzburg" canonicalises to
    # "Salzburg Hbf" via the directory.
    t = "Salzburg ↔ Wien Hauptbahnhof"
    assert _clean_title_keep_places(t) == "Wien Hauptbahnhof ↔ Salzburg Hbf"

def test_title_ordering_preserved_if_vienna_first() -> None:
    # Case 3: Vienna station first -> keep order; "Bruck/Mur"
    # canonicalises to "Bruck an der Mur".
    t = "Wien Hauptbahnhof ↔ Bruck/Mur"
    assert _clean_title_keep_places(t) == "Wien Hauptbahnhof ↔ Bruck an der Mur"

def test_title_ordering_preserved_if_both_vienna() -> None:
    # Case 4: Both Vienna -> keep order
    t = "Wien Floridsdorf ↔ Wien Meidling"
    assert _clean_title_keep_places(t) == "Wien Floridsdorf ↔ Wien Meidling"

def test_title_ordering_preserved_if_neither_vienna() -> None:
    # Case 5: Neither Vienna -> keep order; both canonicalise now.
    t = "Linz/Donau ↔ Salzburg"
    assert _clean_title_keep_places(t) == "Linz Hbf ↔ Salzburg Hbf"
