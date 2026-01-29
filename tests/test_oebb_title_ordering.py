from src.providers.oebb import _clean_title_keep_places
from src.utils.stations import is_in_vienna, canonical_name, station_info

def test_title_reordering_vienna_first():
    # Case 1: Vienna station second -> should swap
    # "Linz/Donau" (unknown/outer) <-> "Wien Hauptbahnhof" (known/vienna)
    # Note: "Linz/Donau" is not in stations.json, so it's treated as raw string (non-Vienna)
    t = "Linz/Donau ↔ Wien Hauptbahnhof"
    # Current behavior (before fix): Keeps order "Linz/Donau ↔ Wien Hauptbahnhof"
    # Desired behavior: "Wien Hauptbahnhof ↔ Linz/Donau"
    assert _clean_title_keep_places(t) == "Wien Hauptbahnhof ↔ Linz/Donau"

def test_title_reordering_vienna_first_salzburg():
    # Case 2: Vienna station second
    t = "Salzburg ↔ Wien Hauptbahnhof"
    assert _clean_title_keep_places(t) == "Wien Hauptbahnhof ↔ Salzburg"

def test_title_ordering_preserved_if_vienna_first():
    # Case 3: Vienna station first -> keep
    t = "Wien Hauptbahnhof ↔ Bruck/Mur"
    # Bruck/Mur might be "Bruck an der Mur" or just unknown.
    # Assuming "Wien Hauptbahnhof" is Vienna.
    assert _clean_title_keep_places(t) == "Wien Hauptbahnhof ↔ Bruck/Mur"

def test_title_ordering_preserved_if_both_vienna():
    # Case 4: Both Vienna -> keep order
    t = "Wien Floridsdorf ↔ Wien Meidling"
    assert _clean_title_keep_places(t) == "Wien Floridsdorf ↔ Wien Meidling"

def test_title_ordering_preserved_if_neither_vienna():
    # Case 5: Neither Vienna -> keep order
    t = "Linz/Donau ↔ Salzburg"
    assert _clean_title_keep_places(t) == "Linz/Donau ↔ Salzburg"
