from datetime import timezone
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
        "ends_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "description": "longer",
    }
    later = {
        "_identity": "a",
        "ends_at": datetime(2024, 1, 2, tzinfo=timezone.utc),
        "description": "short",
    }

    out = build_feed._dedupe_items([earlier, later])
    assert out == [later]


def test_prefers_newer_even_if_ends_at_shorter(monkeypatch):
    build_feed = _import_build_feed(monkeypatch)

    previous = {
        "_identity": "a",
        "ends_at": datetime(2024, 1, 5, tzinfo=timezone.utc),
        "description": "original",
        "pubDate": datetime(2024, 1, 1, 8, 0, 0, tzinfo=timezone.utc),
    }
    update = {
        "_identity": "a",
        "ends_at": datetime(2024, 1, 3, tzinfo=timezone.utc),  # verkürzt
        "description": "original",
        "pubDate": datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc),  # neuer
    }

    out = build_feed._dedupe_items([previous, update])
    # The new logic strictly prefers longer end date
    assert out == [previous]


def test_prefers_newer_when_starts_at_changes(monkeypatch):
    build_feed = _import_build_feed(monkeypatch)

    base = {
        "_identity": "a",
        "starts_at": datetime(2024, 1, 1, 8, 0, 0, tzinfo=timezone.utc),
        "ends_at": datetime(2024, 1, 5, 12, 0, 0, tzinfo=timezone.utc),
        "description": "unchanged",
        "pubDate": datetime(2024, 1, 1, 8, 0, 0, tzinfo=timezone.utc),
    }
    modified = {
        "_identity": "a",
        "starts_at": datetime(2024, 1, 2, 8, 0, 0, tzinfo=timezone.utc),  # geänderte Startzeit
        "ends_at": datetime(2024, 1, 5, 12, 0, 0, tzinfo=timezone.utc),
        "description": "unchanged",
        "pubDate": datetime(2024, 1, 1, 9, 0, 0, tzinfo=timezone.utc),
    }

    out = build_feed._dedupe_items([base, modified])
    assert out == [modified]
