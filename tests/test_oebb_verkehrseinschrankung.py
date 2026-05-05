from src.providers.oebb import _clean_title_keep_places

def test_verkehrseinschaenkung_title() -> None:
    t = "Verkehrseinschränkung: Wien Meidling und Tullnerfeld"
    res = _clean_title_keep_places(t)
    assert res == "Wien Meidling ↔ Tullnerfeld"

def test_verkehrseinschaenkung_with_other_stations() -> None:
    t = "Wien Hbf und St. Pölten Hbf"
    res = _clean_title_keep_places(t)
    # Canonical names use the full "Hauptbahnhof" form for both stations.
    assert res == "Wien Hauptbahnhof ↔ St. Pölten Hauptbahnhof"
