from src.providers.oebb import _keep_by_region


def test_whitelist_deutsch_wagram():
    assert _keep_by_region("Wien ↔ Deutsch Wagram Bahnhof", "")


def test_whitelist_ebenfurth():
    assert _keep_by_region("Wien ↔ Ebenfurth Bahnhof", "")
