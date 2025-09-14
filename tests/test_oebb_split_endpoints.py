import src.providers.oebb as oebb


def test_split_endpoints_deduplicates():
    title = "Wien Hbf ↔ Wien Hbf"
    assert oebb._split_endpoints(title) == ["Wien"]

