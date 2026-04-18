import importlib
import sys
import types
from pathlib import Path


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
    # Ensure config is reloaded to pick up new env vars/paths
    sys.modules.pop("feed", None)
    sys.modules.pop("feed.config", None)
    sys.modules.pop("src.feed", None)
    sys.modules.pop("src.feed.config", None)
    return importlib.import_module(module_name)


def test_main_does_not_save_state_on_io_error(monkeypatch, tmp_path, caplog):
    build_feed = _import_build_feed(monkeypatch)

    monkeypatch.chdir(tmp_path)
    out_file = tmp_path / "feed.xml"
    state_file = tmp_path / "state.json"

    monkeypatch.setattr(build_feed.feed_config, "OUT_PATH", out_file)
    monkeypatch.setattr(build_feed.feed_config, "STATE_FILE", state_file)
    monkeypatch.setattr(build_feed, "validate_path", lambda path, name: path)

    # Disable network/cache
    monkeypatch.setenv("WL_ENABLE", "0")
    monkeypatch.setenv("OEBB_ENABLE", "0")
    monkeypatch.setenv("VOR_ENABLE", "0")
    monkeypatch.setenv("BAUSTELLEN_ENABLE", "0")
    build_feed.refresh_from_env()

    # Track if save_state was called
    save_state_called = False

    def spy_save_state(state, deletions=None):
        nonlocal save_state_called
        save_state_called = True

    monkeypatch.setattr(build_feed, "_save_state", spy_save_state)

    # Mock atomic_write to raise IOError
    def failing_atomic_write(path, mode="w", encoding="utf-8", permissions=0o644):
        class FailingContextManager:
            def __enter__(self):
                raise IOError("Simulated IO Error during write")
            def __exit__(self, exc_type, exc_val, exc_tb):
                pass
        return FailingContextManager()

    monkeypatch.setattr(build_feed, "atomic_write", failing_atomic_write)

    # We expect main to raise the IOError
    try:
        build_feed.main()
        assert False, "Expected IOError to be raised"
    except IOError as e:
        assert "Simulated IO Error" in str(e)

    # Verify that state was NOT saved because the write failed
    assert not save_state_called
