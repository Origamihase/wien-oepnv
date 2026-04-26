from src.providers.oebb import _clean_title_keep_places


def test_wien_und_arrow_and_clean():
    t = "Verkehrsmeldung: Wien Floridsdorf Bahnhof und Wien Meidling Hbf"
    assert _clean_title_keep_places(t) == "Wien Floridsdorf ↔ Wien Meidling"


def test_clean_title_canonicalizes_endpoints():
    t = "Verkehrsmeldung: Wien Franz Josefs Bahnhof - St Poelten Hbf"
    assert _clean_title_keep_places(t) == "Wien Franz-Josefs-Bf ↔ St.Pölten Hbf"


def test_clean_title_expands_wien_hbf_abbreviation():
    t = "Störung: Wien Hbf (U) <-> Wien Meidling Bahnhof"
    assert _clean_title_keep_places(t) == "Wien Hauptbahnhof ↔ Wien Meidling"


def test_clean_title_removes_redundant_suffix():
    # Issue: Station name is duplicated in the feed title if it's already part of the message text.
    t = "Bahnsteig 2/3 in Sigmundsherberg nicht barrierefrei: Sigmundsherberg"
    assert _clean_title_keep_places(t) == "Bahnsteig 2/3 in Sigmundsherberg nicht barrierefrei"
