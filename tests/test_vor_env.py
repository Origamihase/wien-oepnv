import importlib
import src.providers.vor as vor


def test_access_id_env_normalization(monkeypatch):
    # VOR_ACCESS_ID mit Leerzeichen sollte als None interpretiert werden
    monkeypatch.setenv("VOR_ACCESS_ID", "   ")
    importlib.reload(vor)
    assert vor.VOR_ACCESS_ID is None

    # Fallback auf VAO_ACCESS_ID, ebenfalls Leerzeichen -> None
    monkeypatch.delenv("VOR_ACCESS_ID", raising=False)
    monkeypatch.setenv("VAO_ACCESS_ID", "   ")
    importlib.reload(vor)
    assert vor.VOR_ACCESS_ID is None

    # VAO_ACCESS_ID mit zusätzlichen Leerzeichen wird getrimmt
    monkeypatch.setenv("VAO_ACCESS_ID", " token ")
    importlib.reload(vor)
    assert vor.VOR_ACCESS_ID == "token"

    # Aufräumen
    monkeypatch.delenv("VAO_ACCESS_ID", raising=False)
    importlib.reload(vor)

