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
    monkeypatch.delenv("OUT_PATH", raising=False)
    monkeypatch.delenv("LOG_DIR", raising=False)
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
    module.refresh_from_env()  # Ensure config is fresh
    return module


def test_state_path_override(monkeypatch, tmp_path):
    state_file = tmp_path / "data" / "custom_state.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STATE_PATH", "data/custom_state.json")
    build_feed = _import_build_feed(monkeypatch)
    now = datetime.now(timezone.utc).isoformat()
    build_feed._save_state({"id": {"first_seen": now}})
    assert state_file.exists()
    assert build_feed._load_state() == {"id": {"first_seen": now}}


def test_state_keeps_valid_entries_and_drops_malformed(monkeypatch, tmp_path):
    state_file = tmp_path / "data" / "state.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STATE_PATH", "data/state.json")
    monkeypatch.setenv("STATE_RETENTION_DAYS", "0")
    build_feed = _import_build_feed(monkeypatch)

    old_dt = datetime.now(timezone.utc) - timedelta(days=2)
    new_dt = datetime.now(timezone.utc)
    state_payload = {
        "old": {"first_seen": old_dt.isoformat()},
        "new": {"first_seen": new_dt.isoformat()},
    }
    build_feed._save_state(state_payload)
    data = json.loads(state_file.read_text())
    assert data == state_payload

    with state_file.open("w", encoding="utf-8") as f:
        json.dump(
            {
                **state_payload,
                "broken": {"first_seen": "not-a-date"},
                "missing": {},
                "none": {"first_seen": None},
            },
            f,
        )

    assert build_feed._load_state() == state_payload


def test_state_retention_discards_old_entries(monkeypatch, tmp_path):
    state_file = tmp_path / "data" / "state.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STATE_PATH", "data/state.json")
    monkeypatch.setenv("STATE_RETENTION_DAYS", "1")
    build_feed = _import_build_feed(monkeypatch)

    old_dt = datetime.now(timezone.utc) - timedelta(days=2)
    fresh_dt = datetime.now(timezone.utc)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with state_file.open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "old": {"first_seen": old_dt.isoformat()},
                "fresh": {"first_seen": fresh_dt.isoformat()},
            },
            handle,
        )

    state = build_feed._load_state()

    assert "old" not in state
    assert state == {"fresh": {"first_seen": fresh_dt.isoformat()}}


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
