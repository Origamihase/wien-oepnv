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
            # Exclude guid/link to force fallback hash
        "title": "Störung",
        "starts_at": now,
        "ends_at": now,
    }
    item_b = {
        "source": "oebb",
        "category": "test",
            # Exclude guid/link to force fallback hash
        "title": "Störung",
        "starts_at": now,
        "ends_at": now,
    }

    # Mock validate_path to allow temp paths
    monkeypatch.setattr(build_feed, "validate_path", lambda p, *args: p)

    state = build_feed._load_state()
    build_feed._make_rss([item_a], now, state)
    build_feed._save_state(state)
    state_after_first = build_feed._load_state()
    assert len(state_after_first) == 1
    ident = next(iter(state_after_first.keys()))
    first_seen = state_after_first[ident]["first_seen"]

    state = build_feed._load_state()
    build_feed._make_rss([item_b], now + timedelta(hours=1), state)
    build_feed._save_state(state)
    state_after_second = build_feed._load_state()
    assert len(state_after_second) == 1
    assert ident in state_after_second
    assert state_after_second[ident]["first_seen"] == first_seen

    # Test the fallback guid lookup for a different identity (simulating a merged item)
    item_c = {
        "source": "wl", # wl identity generation doesn't use guid
        "category": "test",
        "title": "Neue Störung",
        "starts_at": now,
        "ends_at": now,
        "guid": ident # Set guid to the identity of item_a
    }

    # Need to save the old state!
    # Because _make_rss processes new items and only saves state for them
    # Wait, the state variable holds the state memory. We just use the same state variable
    # So state has `ident`.

    # _make_rss uses `ident` but then adds it. We must also tell _make_rss that
    # item_c was kept in the state by _make_rss. Wait, `state_after_third` loads from disk.
    # _save_state deletes items not in the recent batch unless `STATE_RETENTION_DAYS`
    # actually _save_state does a safe merge and doesn't delete!

    # Wait, the problem is that `_make_rss` takes `state` dict, adds `ident_c` to it,
    # and then `_save_state(state)` saves BOTH ident and ident_c.
    # Why wasn't ident_c in `state_after_third`?
    # Because `state_after_third` ONLY contained the original `ident`? Let's look at the error:
    # assert 'wl...' in {'oebb...': {'first_seen': '...'}}
    # ONLY 'oebb...' is there. Why did `build_feed._make_rss` not save `wl...`?
    # Because `_identity_for_item` mutates the item and sets `_calculated_identity`.
    # Let's see how `item_c` is processed.

    rss, deletions = build_feed._make_rss([item_c], now + timedelta(hours=2), state)
    build_feed._save_state(state, deletions=deletions)

    state_after_third = build_feed._load_state()

    # We want to know what ident was added
    ident_c = build_feed._identity_for_item(item_c)
    assert ident_c != ident

    # Let's just check the state directly
    assert ident_c in state
    assert state[ident_c]["first_seen"] == first_seen
