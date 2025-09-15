import importlib
import sys
from pathlib import Path
import types


def _import_build_feed(monkeypatch):
    module_name = "src.build_feed"
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.syspath_prepend(str(root / "src"))
    providers = types.ModuleType("providers")
    wl = types.ModuleType("providers.wiener_linien")
    wl.fetch_events = lambda: []
    oebb = types.ModuleType("providers.oebb")
    oebb.fetch_events = lambda: []
    vor = types.ModuleType("providers.vor")
    vor.fetch_events = lambda: []
    monkeypatch.setitem(sys.modules, "providers", providers)
    monkeypatch.setitem(sys.modules, "providers.wiener_linien", wl)
    monkeypatch.setitem(sys.modules, "providers.oebb", oebb)
    monkeypatch.setitem(sys.modules, "providers.vor", vor)
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


def test_default_age_and_ttl(monkeypatch):
    monkeypatch.delenv("FEED_TTL", raising=False)
    monkeypatch.delenv("MAX_ITEM_AGE_DAYS", raising=False)
    monkeypatch.delenv("ABSOLUTE_MAX_AGE_DAYS", raising=False)
    build_feed = _import_build_feed(monkeypatch)
    assert build_feed.FEED_TTL == 15
    assert build_feed.MAX_ITEM_AGE_DAYS == 365
    assert build_feed.ABSOLUTE_MAX_AGE_DAYS == 540
