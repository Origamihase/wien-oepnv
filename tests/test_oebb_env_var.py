import importlib

import src.providers.oebb as oebb


def test_oebb_only_vienna_env_var(monkeypatch):
    monkeypatch.setenv("OEBB_ONLY_VIENNA", "FaLsE")
    importlib.reload(oebb)
    assert oebb.OEBB_ONLY_VIENNA is False
    monkeypatch.setenv("OEBB_ONLY_VIENNA", "1")
    importlib.reload(oebb)
    assert oebb.OEBB_ONLY_VIENNA is True
    monkeypatch.setenv("OEBB_ONLY_VIENNA", "yes")
    importlib.reload(oebb)
    assert oebb.OEBB_ONLY_VIENNA is True
    monkeypatch.delenv("OEBB_ONLY_VIENNA", raising=False)
    importlib.reload(oebb)

