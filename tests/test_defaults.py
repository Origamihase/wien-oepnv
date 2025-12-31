import importlib
import sys
from pathlib import Path
import types
import pytest


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


@pytest.mark.parametrize(
    "attr, expected",
    [
        ("FEED_TTL", 15),
        ("MAX_ITEM_AGE_DAYS", 365),
        ("ABSOLUTE_MAX_AGE_DAYS", 540),
    ],
)
def test_default_age_and_ttl(monkeypatch, attr, expected):
    monkeypatch.delenv("FEED_TTL", raising=False)
    monkeypatch.delenv("MAX_ITEM_AGE_DAYS", raising=False)
    monkeypatch.delenv("ABSOLUTE_MAX_AGE_DAYS", raising=False)
    build_feed = _import_build_feed(monkeypatch)
    # Refactored build_feed accesses constants via feed_config
    assert getattr(build_feed.feed_config, attr) == expected
