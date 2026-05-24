import importlib
import sys
import pytest
import types
from pathlib import Path
from datetime import datetime, timedelta, UTC


def _import_build_feed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> types.ModuleType:
    module_name = "src.build_feed"
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.syspath_prepend(str(root / "src"))
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
    # Ensure config is reloaded to pick up new env vars/paths
    sys.modules.pop("feed", None)
    sys.modules.pop("feed.config", None)
    sys.modules.pop("src.feed", None)
    sys.modules.pop("src.feed.config", None)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STATE_PATH", "data/state_fuzzy.json")
    return importlib.import_module(module_name)


def test_first_seen_fuzzy_identity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    build_feed = _import_build_feed(monkeypatch, tmp_path)
    now = datetime.now(UTC)
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

    # Guid-stable first_seen: an item with a guid is keyed on that guid, so
    # its first_seen survives a title change (e.g. the read-side ÖPNV title
    # enrichment) instead of resetting.
    item_c1 = {
        "source": "wl",
        "category": "test",
        "title": "Linie 9 gestört",
        "starts_at": now,
        "ends_at": now,
        "guid": "WL-12345",
    }
    state = build_feed._load_state()
    build_feed._make_rss([item_c1], now + timedelta(hours=2), state)
    build_feed._save_state(state)
    state = build_feed._load_state()
    assert "WL-12345" in state  # keyed on the guid, not the title identity
    fs_c = state["WL-12345"]["first_seen"]

    # Same guid, changed title → first_seen preserved, no second entry.
    item_c2 = dict(item_c1, title="9: Linie 9 gestört wegen Bauarbeiten")
    state = build_feed._load_state()
    build_feed._make_rss([item_c2], now + timedelta(hours=3), state)
    build_feed._save_state(state)
    state = build_feed._load_state()
    assert "WL-12345" in state
    assert state["WL-12345"]["first_seen"] == fs_c
    assert build_feed._identity_for_item(item_c2) not in state
