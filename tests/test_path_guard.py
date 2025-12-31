import importlib
import sys
import types
from pathlib import Path
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
    monkeypatch.setitem(sys.modules, "providers", providers)
    monkeypatch.setitem(sys.modules, "providers.wiener_linien", wl)
    monkeypatch.setitem(sys.modules, "providers.oebb", oebb)
    sys.modules.pop(module_name, None)
    module = importlib.import_module(module_name)
    module.refresh_from_env()
    return module


def test_out_path_rejects_outside_whitelist(monkeypatch, tmp_path):
    build_feed = _import_build_feed(monkeypatch)
    monkeypatch.chdir(tmp_path)
    # Set env var so refresh_from_env picks it up and validation fails
    monkeypatch.setenv("OUT_PATH", "../evil.xml")
    monkeypatch.setattr(build_feed, "_collect_items", lambda: [])
    monkeypatch.setattr(build_feed, "_make_rss", lambda items, now, state: "")
    monkeypatch.setattr(build_feed, "_load_state", lambda: {})
    monkeypatch.setattr(build_feed, "_save_state", lambda state: None)

    with pytest.raises(ValueError):
        build_feed.main()


def test_state_path_rejects_outside_whitelist(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STATE_PATH", "../evil.json")
    with pytest.raises(ValueError):
        _import_build_feed(monkeypatch)


def test_log_dir_outside_whitelist_falls_back(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LOG_DIR", "../evil")
    build_feed = _import_build_feed(monkeypatch)

    assert build_feed.feed_config.LOG_DIR_PATH.name == "log"
    assert (tmp_path / "log").is_dir() or (tmp_path / "log").parent.exists()
