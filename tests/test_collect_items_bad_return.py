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
    wl.fetch_events = lambda timeout=None: []
    oebb = types.ModuleType("providers.oebb")
    oebb.fetch_events = lambda timeout=None: []
    vor = types.ModuleType("providers.vor")
    vor.fetch_events = lambda timeout=None: []
    monkeypatch.setitem(sys.modules, "providers", providers)
    monkeypatch.setitem(sys.modules, "providers.wiener_linien", wl)
    monkeypatch.setitem(sys.modules, "providers.oebb", oebb)
    monkeypatch.setitem(sys.modules, "providers.vor", vor)
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


def test_collect_items_logs_and_skips_invalid_return(monkeypatch, caplog):
    build_feed = _import_build_feed(monkeypatch)

    def good_provider(timeout=None):
        return [{"p": "good"}]

    def bad_provider(timeout=None):
        return {"p": "bad"}

    monkeypatch.setattr(
        build_feed,
        "PROVIDERS",
        [
            ("GOOD_ENABLE", good_provider),
            ("BAD_ENABLE", bad_provider),
        ],
    )

    monkeypatch.setenv("GOOD_ENABLE", "1")
    monkeypatch.setenv("BAD_ENABLE", "1")

    with caplog.at_level("ERROR"):
        items = build_feed._collect_items()

    assert items == [{"p": "good"}]
    assert any("bad_provider" in r.getMessage() for r in caplog.records)
