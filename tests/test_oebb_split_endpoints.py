import src.providers.oebb as oebb


def test_split_endpoints_deduplicates():
    title = "Wien Hbf ↔ Wien Hbf"
    assert oebb._split_endpoints(title) == ["Wien"]


def test_split_endpoints_hyphen():
    title = "Wien - Linz"
    assert oebb._split_endpoints(title) == ["Wien", "Linz"]

