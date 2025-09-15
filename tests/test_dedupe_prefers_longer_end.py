import importlib
import sys
from pathlib import Path
import types
from datetime import datetime


def _import_build_feed(monkeypatch):
    module_name = "src.build_feed"
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.syspath_prepend(str(root / "src"))
    # Provide lightweight provider stubs to avoid heavy deps during import
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


def test_prefers_later_ends_at(monkeypatch):
    build_feed = _import_build_feed(monkeypatch)

    earlier = {
        "_identity": "a",
        "ends_at": datetime(2024, 1, 1),
        "description": "longer",
    }
    later = {
        "_identity": "a",
        "ends_at": datetime(2024, 1, 2),
        "description": "short",
    }

    out = build_feed._dedupe_items([earlier, later])
    assert out == [later]
