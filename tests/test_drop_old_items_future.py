import importlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import types


def _import_build_feed(monkeypatch, env=None):
    module_name = "src.build_feed"
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.syspath_prepend(str(root / "src"))
    providers = types.ModuleType("providers")
    wl = types.ModuleType("providers.wiener_linien")
    wl.fetch_events = lambda: []
    oebb = types.ModuleType("providers.oebb")
    oebb.fetch_events = lambda: []
    monkeypatch.setitem(sys.modules, "providers", providers)
    monkeypatch.setitem(sys.modules, "providers.wiener_linien", wl)
    monkeypatch.setitem(sys.modules, "providers.oebb", oebb)
    env = env or {}
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


def test_future_ends_at_skips_max_age(monkeypatch):
    build_feed = _import_build_feed(
        monkeypatch,
        {"MAX_ITEM_AGE_DAYS": "365", "ABSOLUTE_MAX_AGE_DAYS": "540"},
    )
    now = datetime.now(timezone.utc)
    future = {
        "title": "future",
        "pubDate": now - timedelta(days=400),
        "ends_at": now + timedelta(days=1),
    }
    no_end = {"title": "no_end", "pubDate": now - timedelta(days=400)}
    too_old = {
        "title": "too_old",
        "pubDate": now - timedelta(days=541),
        "ends_at": now + timedelta(days=1),
    }
    res = build_feed._drop_old_items([future, no_end, too_old], now)
    assert res == [future]
