import importlib
import sys
import time
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


def test_collect_items_timeout(monkeypatch, caplog):
    monkeypatch.setenv("PROVIDER_TIMEOUT", "0.01")
    build_feed = _import_build_feed(monkeypatch)

    def slow_provider():
        time.sleep(0.5)
        return [{"p": "slow"}]

    def fast_provider():
        return [{"p": "fast"}]

    monkeypatch.setattr(
        build_feed,
        "PROVIDERS",
        [
            ("SLOW_ENABLE", slow_provider),
            ("FAST_ENABLE", fast_provider),
        ],
    )

    monkeypatch.setenv("SLOW_ENABLE", "1")
    monkeypatch.setenv("FAST_ENABLE", "1")

    with caplog.at_level("WARNING"):
        items = build_feed._collect_items()

    assert items == [{"p": "fast"}]
    assert any("Timeout" in r.getMessage() for r in caplog.records)
