import src.providers.oebb as oebb


def test_whitelist_deutsch_wagram():
    assert oebb._keep_by_region("Wien ↔ Deutsch Wagram Bahnhof", "")


def test_whitelist_ebenfurth():
    assert oebb._keep_by_region("Wien ↔ Ebenfurth Bahnhof", "")


def test_only_vienna_env(monkeypatch):
    monkeypatch.setattr(oebb, "OEBB_ONLY_VIENNA", True)
    assert not oebb._keep_by_region("Wien ↔ Deutsch Wagram Bahnhof", "")
    assert oebb._keep_by_region("Wien Floridsdorf ↔ Wien Mitte", "")
