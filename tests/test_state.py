import importlib
import sys
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
import types


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
    return importlib.import_module(module_name)


def test_state_path_override(monkeypatch, tmp_path):
    state_file = tmp_path / "data" / "custom_state.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STATE_PATH", "data/custom_state.json")
    build_feed = _import_build_feed(monkeypatch)
    now = datetime.now(timezone.utc).isoformat()
    build_feed._save_state({"id": {"first_seen": now}})
    assert state_file.exists()
    assert build_feed._load_state() == {"id": {"first_seen": now}}


def test_state_retention_drops_old_entries(monkeypatch, tmp_path):
    state_file = tmp_path / "data" / "state.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STATE_PATH", "data/state.json")
    monkeypatch.setenv("STATE_RETENTION_DAYS", "1")
    build_feed = _import_build_feed(monkeypatch)

    old_dt = datetime.now(timezone.utc) - timedelta(days=2)
    new_dt = datetime.now(timezone.utc)
    build_feed._save_state(
        {
            "old": {"first_seen": old_dt.isoformat()},
            "new": {"first_seen": new_dt.isoformat()},
        }
    )
    data = json.loads(state_file.read_text())
    assert "old" not in data and "new" in data

    with state_file.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "old": {"first_seen": old_dt.isoformat()},
                "new": {"first_seen": new_dt.isoformat()},
            },
            f,
        )
    assert build_feed._load_state() == {"new": {"first_seen": new_dt.isoformat()}}


def test_state_cleared_when_feed_empty(monkeypatch, tmp_path):
    state_file = tmp_path / "data" / "state.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STATE_PATH", "data/state.json")
    build_feed = _import_build_feed(monkeypatch)
    now = datetime.now(timezone.utc)
    build_feed._save_state({"id": {"first_seen": now.isoformat()}})

    # ensure state file has content before running
    assert json.loads(state_file.read_text()) != {}

    state = build_feed._load_state()
    build_feed._make_rss([], now, state)

    assert json.loads(state_file.read_text()) == {}
