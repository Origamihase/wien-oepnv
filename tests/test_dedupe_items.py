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
    vor = types.ModuleType("providers.vor")
    vor.fetch_events = lambda: []
    monkeypatch.setitem(sys.modules, "providers", providers)
    monkeypatch.setitem(sys.modules, "providers.wiener_linien", wl)
    monkeypatch.setitem(sys.modules, "providers.oebb", oebb)
    monkeypatch.setitem(sys.modules, "providers.vor", vor)
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


def test_main_dedupes_items(monkeypatch, tmp_path):
    build_feed = _import_build_feed(monkeypatch)

    sample_items = [
        {"_identity": "a", "guid": "ga", "title": "A"},
        {"_identity": "a", "guid": "ga_dup", "title": "A2"},
        {"guid": "gb", "title": "B"},
        {"guid": "gb", "title": "B2"},
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
    monkeypatch.chdir(tmp_path)
    build_feed.OUT_PATH = "docs/feed.xml"

    build_feed.main()

    # Remove internal cache keys from captured items to allow comparison
    for item in captured["items"]:
        item.pop("_calculated_identity", None)
        item.pop("_calculated_dedupe_key", None)
        item.pop("_calculated_recency", None)
        item.pop("_calculated_end", None)

    assert captured["items"] == [
        {"_identity": "a", "guid": "ga", "title": "A"},
        {"guid": "gb", "title": "B"},
        {"guid": "gc", "title": "C"},
    ]


def test_items_without_identifier_are_unique(monkeypatch):
    build_feed = _import_build_feed(monkeypatch)

    items = [
        {"title": "A", "description": "desc1"},
        {"title": "B", "description": "desc2"},
    ]

    assert build_feed._dedupe_items(items) == items


def test_items_with_same_text_but_different_source(monkeypatch):
    build_feed = _import_build_feed(monkeypatch)

    items = [
        {"title": "A", "description": "desc", "source": "S1"},
        {"title": "A", "description": "desc", "source": "S2"},
    ]

    assert build_feed._dedupe_items(items) == items
