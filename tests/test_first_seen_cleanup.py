import importlib
import sys
import pytest
import types
from pathlib import Path
from datetime import datetime, timezone


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
    monkeypatch.setenv("STATE_PATH", "data/state_cleanup.json")

    return importlib.import_module(module_name)


def test_state_cleanup_keeps_only_current_identities(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    build_feed = _import_build_feed(monkeypatch, tmp_path)
    now = datetime.now(timezone.utc)

    item_a = {"guid": "guid-a", "_identity": "guid-a"}
    item_b = {"guid": "guid-b", "_identity": "guid-b"}

    # Mock validate_path to allow temp paths
    monkeypatch.setattr(build_feed, "validate_path", lambda p, *args: p)

    state = build_feed._load_state()
    build_feed._make_rss([item_a, item_b], now, state)
    build_feed._save_state(state)

    state_after_first = build_feed._load_state()
    assert set(state_after_first.keys()) == {"guid-a", "guid-b"}

    state = build_feed._load_state()
    build_feed._make_rss([item_b], now, state, deletions={"guid-a"})
    build_feed._save_state(state, deletions={"guid-a"})

    state_after_second = build_feed._load_state()
    # Behavior changed: items are no longer aggressively pruned
    assert set(state_after_second.keys()) == {"guid-b"}
