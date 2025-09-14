import importlib
import sys
from pathlib import Path
import types


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
    monkeypatch.setitem(sys.modules, "providers", providers)
    monkeypatch.setitem(sys.modules, "providers.wiener_linien", wl)
    monkeypatch.setitem(sys.modules, "providers.oebb", oebb)
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


def test_main_dedupes_items(monkeypatch, tmp_path):
    build_feed = _import_build_feed(monkeypatch)

    sample_items = [
        {"_identity": "a", "guid": "ga", "title": "A"},
        {"_identity": "a2", "guid": "ga", "title": "A2"},
        {"_identity": "b", "title": "B"},
        {"_identity": "b", "title": "B2"},
        {"guid": "gc", "title": "C"},
    ]

    def fake_collect():
        return list(sample_items)

    captured = {}

    def fake_make_rss(items, now, state):
        captured["items"] = items
        return ""

    monkeypatch.setattr(build_feed, "_collect_items", fake_collect)
    monkeypatch.setattr(build_feed, "_make_rss", fake_make_rss)
    build_feed.OUT_PATH = str(tmp_path / "feed.xml")

    build_feed.main()

    assert captured["items"] == [
        {"_identity": "b", "title": "B"},
        {"_identity": "a", "guid": "ga", "title": "A"},
        {"guid": "gc", "title": "C"},
    ]


def test_guid_takes_precedence_over_identity(monkeypatch):
    build_feed = _import_build_feed(monkeypatch)

    sample_items = [
        {"guid": "same", "_identity": "id1"},
        {"guid": "same", "_identity": "id2"},
    ]

    assert build_feed._dedupe_items(sample_items) == [
        {"guid": "same", "_identity": "id1"}
    ]
