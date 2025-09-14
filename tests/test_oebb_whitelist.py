import src.providers.oebb as oebb
import pytest


@pytest.mark.parametrize("arrow", ["↔", "<->", "->", "—", "–", "→"])
def test_whitelist_deutsch_wagram(arrow: str) -> None:
    assert oebb._keep_by_region(f"Wien {arrow} Deutsch Wagram Bahnhof", "")


def test_whitelist_ebenfurth():
    assert oebb._keep_by_region("Wien ↔ Ebenfurth Bahnhof", "")


def test_only_vienna_env(monkeypatch):
    monkeypatch.setattr(oebb, "OEBB_ONLY_VIENNA", True)
    assert not oebb._keep_by_region("Wien ↔ Deutsch Wagram Bahnhof", "")
    assert oebb._keep_by_region("Wien Floridsdorf ↔ Wien Mitte", "")
