from src.providers.oebb import _clean_title_keep_places

def test_verkehrseinschaenkung_title():
    t = "Verkehrseinschränkung: Wien Meidling und Tullnerfeld"
    res = _clean_title_keep_places(t)
    assert res == "Wien Meidling ↔ Tullnerfeld"

def test_verkehrseinschaenkung_with_other_stations():
    t = "Wien Hbf und St. Pölten Hbf"
    res = _clean_title_keep_places(t)
    # The clean title keep places canonicalizes "Wien Hbf" to "Wien Hauptbahnhof" and "St. Pölten Hbf" to "St.Pölten Hbf"
    assert res == "Wien Hauptbahnhof ↔ St.Pölten Hbf"
