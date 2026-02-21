import importlib
import sys
import types
from pathlib import Path
from datetime import datetime, timezone, timedelta


def _import_build_feed(monkeypatch, tmp_path):
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
    # Ensure config is reloaded to pick up new env vars/paths
    sys.modules.pop("feed", None)
    sys.modules.pop("feed.config", None)
    sys.modules.pop("src.feed", None)
    sys.modules.pop("src.feed.config", None)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STATE_PATH", "data/state_fuzzy.json")
    return importlib.import_module(module_name)


def test_first_seen_fuzzy_identity(monkeypatch, tmp_path):
    build_feed = _import_build_feed(monkeypatch, tmp_path)
    now = datetime.now(timezone.utc)
    item_a = {
        "source": "oebb",
        "category": "test",
        "guid": "A",
        "title": "Störung",
        "starts_at": now,
        "ends_at": now,
    }
    item_b = {
        "source": "oebb",
        "category": "test",
        "guid": "B",
        "title": "Störung",
        "starts_at": now,
        "ends_at": now,
    }

    # Mock validate_path to allow temp paths
    monkeypatch.setattr(build_feed, "validate_path", lambda p, *args: p)

    state = build_feed._load_state()
    build_feed._make_rss([item_a], now, state)
    state_after_first = build_feed._load_state()
    assert len(state_after_first) == 1
    ident = next(iter(state_after_first.keys()))
    first_seen = state_after_first[ident]["first_seen"]

    state = build_feed._load_state()
    build_feed._make_rss([item_b], now + timedelta(hours=1), state)
    state_after_second = build_feed._load_state()
    assert len(state_after_second) == 1
    assert ident in state_after_second
    assert state_after_second[ident]["first_seen"] == first_seen
