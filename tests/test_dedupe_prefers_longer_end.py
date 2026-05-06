import importlib
import sys
from pathlib import Path
import pytest
import types
from datetime import datetime, UTC


def _import_build_feed(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    module_name = "src.build_feed"
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.syspath_prepend(str(root / "src"))
    # Provide lightweight provider stubs to avoid heavy deps during import
    providers = types.ModuleType("providers")
    wl = types.ModuleType("providers.wiener_linien")
    setattr(wl, "fetch_events", lambda: [])
    oebb = types.ModuleType("providers.oebb")
    setattr(oebb, "fetch_events", lambda: [])
    vor = types.ModuleType("providers.vor")
    setattr(vor, "fetch_events", lambda: [])
    monkeypatch.setitem(sys.modules, "providers", providers)
    monkeypatch.setitem(sys.modules, "providers.wiener_linien", wl)
    monkeypatch.setitem(sys.modules, "providers.oebb", oebb)
    monkeypatch.setitem(sys.modules, "providers.vor", vor)
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


def test_prefers_later_ends_at(monkeypatch: pytest.MonkeyPatch) -> None:
    build_feed = _import_build_feed(monkeypatch)

    earlier = {
        "_identity": "a",
        "ends_at": datetime(2024, 1, 1, tzinfo=UTC),
        "description": "longer",
    }
    later = {
        "_identity": "a",
        "ends_at": datetime(2024, 1, 2, tzinfo=UTC),
        "description": "short",
    }

    out = build_feed._dedupe_items([earlier, later])
    assert out == [later]


def test_prefers_newer_even_if_ends_at_shorter(monkeypatch: pytest.MonkeyPatch) -> None:
    build_feed = _import_build_feed(monkeypatch)

    previous = {
        "_identity": "a",
        "ends_at": datetime(2024, 1, 5, tzinfo=UTC),
        "description": "original",
        "pubDate": datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC),
    }
    update = {
        "_identity": "a",
        "ends_at": datetime(2024, 1, 3, tzinfo=UTC),  # verkürzt
        "description": "original",
        "pubDate": datetime(2024, 1, 1, 10, 0, 0, tzinfo=UTC),  # neuer
    }

    out = build_feed._dedupe_items([previous, update])
    # The new logic strictly prefers longer end date
    assert out == [previous]


def test_prefers_newer_when_starts_at_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    build_feed = _import_build_feed(monkeypatch)

    base = {
        "_identity": "a",
        "starts_at": datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC),
        "ends_at": datetime(2024, 1, 5, 12, 0, 0, tzinfo=UTC),
        "description": "unchanged",
        "pubDate": datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC),
    }
    modified = {
        "_identity": "a",
        "starts_at": datetime(2024, 1, 2, 8, 0, 0, tzinfo=UTC),  # geänderte Startzeit
        "ends_at": datetime(2024, 1, 5, 12, 0, 0, tzinfo=UTC),
        "description": "unchanged",
        "pubDate": datetime(2024, 1, 1, 9, 0, 0, tzinfo=UTC),
    }

    out = build_feed._dedupe_items([base, modified])
    assert out == [modified]
