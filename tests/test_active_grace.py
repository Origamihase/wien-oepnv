import importlib
from datetime import datetime, timedelta, timezone


def test_active_grace_env(monkeypatch):
    monkeypatch.setenv("ACTIVE_GRACE_MIN", "60")
    import src.providers.wiener_linien as wl
    wl = importlib.reload(wl)
    now = datetime.now(timezone.utc)
    assert wl._is_active(None, now - timedelta(minutes=59), now)
    assert not wl._is_active(None, now - timedelta(minutes=61), now)
    monkeypatch.delenv("ACTIVE_GRACE_MIN", raising=False)
    importlib.reload(wl)
