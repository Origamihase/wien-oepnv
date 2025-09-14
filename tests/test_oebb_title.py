from src.providers.oebb import _clean_title_keep_places


def test_wien_und_arrow_and_clean():
    t = "Verkehrsmeldung: Wien Floridsdorf Bahnhof und Wien Meidling Hbf"
    assert _clean_title_keep_places(t) == "Wien Floridsdorf ↔ Wien Meidling"

