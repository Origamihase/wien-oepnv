import sys
import json
import importlib
import types
from pathlib import Path
from unittest.mock import MagicMock
from contextlib import contextmanager

def _import_build_feed(monkeypatch):
    module_name = "src.build_feed"
    # Ensure we can import src modules
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.syspath_prepend(str(root / "src"))

    # Mock providers to avoid import errors
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

def test_save_state_uses_separate_lock_file(monkeypatch, tmp_path):
    """
    Verify that _save_state creates a .lock file instead of locking the target file directly.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STATE_PATH", "data/state.json")
    state_file = tmp_path / "data" / "state.json"

    # Import build_feed with mocked dependencies
    build_feed = _import_build_feed(monkeypatch)

    # Spy on _file_lock to check what file is being locked
    original_file_lock = build_feed._file_lock
    locked_files = []

    @contextmanager
    def spy_file_lock(fileobj, exclusive):
        locked_files.append(fileobj.name)
        with original_file_lock(fileobj, exclusive=exclusive):
            yield

    monkeypatch.setattr(build_feed, "_file_lock", spy_file_lock)

    # Save state
    state_data = {"test": {"first_seen": "2023-01-01T12:00:00+00:00"}}
    build_feed._save_state(state_data)

    # Verify that the lock file was used
    # The lock file path should end with .lock
    assert len(locked_files) == 1
    assert str(locked_files[0]).endswith(".lock")
    assert Path(locked_files[0]).exists() or Path(locked_files[0]).with_suffix("").exists()

    # Verify the actual state file content
    assert json.loads(state_file.read_text()) == state_data
