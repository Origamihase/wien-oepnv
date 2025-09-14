import importlib
import sys
import time
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


def test_slow_provider_does_not_block(monkeypatch):
    monkeypatch.setenv("PROVIDER_TIMEOUT", "1")
    build_feed = _import_build_feed(monkeypatch)

    def slow_fetch(timeout=None):
        time.sleep(2)
        return [{"guid": "slow"}]

    def fast_fetch():
        return [{"guid": "fast"}]

    monkeypatch.setattr(
        build_feed,
        "PROVIDERS",
        [("SLOW", slow_fetch), ("FAST", fast_fetch)],
    )
    monkeypatch.setenv("SLOW", "1")
    monkeypatch.setenv("FAST", "1")

    start = time.monotonic()
    items = build_feed._collect_items()
    elapsed = time.monotonic() - start

    assert items == [{"guid": "fast"}]
    assert elapsed < 2
