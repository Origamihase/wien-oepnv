import importlib

import pytest

import src.providers.oebb as oebb


def test_oebb_only_vienna_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    # mypy narrows OEBB_ONLY_VIENNA after each `assert is True/False`, but the
    # value is reread by importlib.reload() between asserts. Cast to bool
    # before each assertion so mypy does not collapse the variable to a
    # constant and treat subsequent reload paths as unreachable.
    monkeypatch.setenv("OEBB_ONLY_VIENNA", "FaLsE")
    importlib.reload(oebb)
    assert bool(oebb.OEBB_ONLY_VIENNA) is False
    monkeypatch.setenv("OEBB_ONLY_VIENNA", "1")
    importlib.reload(oebb)
    assert bool(oebb.OEBB_ONLY_VIENNA) is True
    monkeypatch.setenv("OEBB_ONLY_VIENNA", "yes")
    importlib.reload(oebb)
    assert bool(oebb.OEBB_ONLY_VIENNA) is True
    monkeypatch.delenv("OEBB_ONLY_VIENNA", raising=False)
    importlib.reload(oebb)

