from src.providers.oebb import _clean_title_keep_places, _is_relevant

def test_clean_title_db_bauarbeiten():
    title = "DB-Bauarbeiten ↔ Umleitung/Haltausfall: Linz/Donau Passau"
    cleaned = _clean_title_keep_places(title)
    assert cleaned == "Linz/Donau ↔ Passau" or cleaned == "Linz/Donau Passau"

def test_is_relevant_db_bauarbeiten():
    title = "DB-Bauarbeiten ↔ Umleitung/Haltausfall: Linz/Donau Passau"
    desc = (
        "Wegen Bauarbeiten der Deutschen Bahn (DB) wird von 22.04. auf 23.04.2026 der Zug NJ 498 "
        "über Salzburg Hbf umgeleitet und kann daher in Passau Hbf und Regensburg Hbf nicht halten."
        "<br/>[Seit 03.03.2026]"
    )

    cleaned = _clean_title_keep_places(title)
    # Linz/Donau and Passau do not have vienna connections and aren't in pendler range
    assert _is_relevant(cleaned, desc) is False

def test_clean_title_compound_category():
    title = "ÖBB-Verspätung ↔ Zugausfall: Wien Hbf ↔ St. Pölten"
    cleaned = _clean_title_keep_places(title)
    # the exact output depends on internal sorting / canonical naming
    # wait, "Wien Hbf" and "St. Pölten" should just be the parts
    assert cleaned == "Wien Hauptbahnhof ↔ St. Pölten" or cleaned == "St.Pölten Hbf ↔ Wien Hauptbahnhof"
