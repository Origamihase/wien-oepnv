import builtins
import importlib
import logging
import sys
from pathlib import Path

def _import_build_feed_without_providers(monkeypatch):
    module_name = "src.build_feed"
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.syspath_prepend(str(root / "src"))

    sys.modules.pop(module_name, None)
    for mod in list(sys.modules):
        if mod == "providers" or mod.startswith("providers."):
            sys.modules.pop(mod, None)

    real_import = builtins.__import__

    def guard(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith("providers"):
            raise AssertionError(f"unexpected provider import: {name}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guard)
    return importlib.import_module(module_name)


def _patch_empty_cache(monkeypatch, tmp_path):
    cache_mod = importlib.import_module("utils.cache")
    monkeypatch.setattr(cache_mod, "_CACHE_DIR", tmp_path / "cache", raising=False)


def test_collect_items_missing_cache_logs_warning(monkeypatch, tmp_path, caplog):
    build_feed = _import_build_feed_without_providers(monkeypatch)
    _patch_empty_cache(monkeypatch, tmp_path)

    caplog.set_level(logging.WARNING, logger="build_feed")
    caplog.set_level(logging.WARNING, logger="utils.cache")

    items = build_feed._collect_items()

    assert items == []

    cache_warnings = {
        record.message
        for record in caplog.records
        if record.name == "build_feed" and "Cache für Provider" in record.message
    }
    assert cache_warnings == {
        "Cache für Provider 'wl' leer – generiere Feed ohne aktuelle Daten.",
        "Cache für Provider 'oebb' leer – generiere Feed ohne aktuelle Daten.",
        "Cache für Provider 'vor' leer – generiere Feed ohne aktuelle Daten.",
    }


def test_main_runs_without_network(monkeypatch, tmp_path, caplog):
    build_feed = _import_build_feed_without_providers(monkeypatch)
    _patch_empty_cache(monkeypatch, tmp_path)

    out_file = tmp_path / "feed.xml"
    state_file = tmp_path / "state.json"

    monkeypatch.setattr(build_feed, "_validate_path", lambda path, name: path)
    monkeypatch.setattr(build_feed, "OUT_PATH", str(out_file))
    monkeypatch.setattr(build_feed, "STATE_FILE", state_file)
    monkeypatch.setattr(build_feed, "_save_state", lambda state: None)
    monkeypatch.setattr(build_feed, "_load_state", lambda: {})

    caplog.set_level(logging.WARNING, logger="build_feed")
    caplog.set_level(logging.WARNING, logger="utils.cache")

    exit_code = build_feed.main()

    assert exit_code == 0
    assert out_file.exists()

    cache_messages = [
        record.message
        for record in caplog.records
        if record.name == "build_feed" and "Cache für Provider" in record.message
    ]
    assert set(cache_messages) == {
        "Cache für Provider 'wl' leer – generiere Feed ohne aktuelle Daten.",
        "Cache für Provider 'oebb' leer – generiere Feed ohne aktuelle Daten.",
        "Cache für Provider 'vor' leer – generiere Feed ohne aktuelle Daten.",
    }


def test_collect_items_reads_from_cache(monkeypatch):
    build_feed = _import_build_feed_without_providers(monkeypatch)

    calls = []

    def fake_read_cache(provider):
        calls.append(provider)
        return [{"provider": provider}]

    monkeypatch.setattr(build_feed, "read_cache", fake_read_cache)
    monkeypatch.setenv("WL_ENABLE", "1")
    monkeypatch.setenv("OEBB_ENABLE", "1")
    monkeypatch.setenv("VOR_ENABLE", "1")

    items = build_feed._collect_items()

    assert len(calls) == 3
    assert set(calls) == {"wl", "oebb", "vor"}
    assert sorted(items, key=lambda item: item["provider"]) == [
        {"provider": "oebb"},
        {"provider": "vor"},
        {"provider": "wl"},
    ]
