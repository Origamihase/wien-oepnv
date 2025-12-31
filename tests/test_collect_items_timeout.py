import importlib
import sys
import threading
import time
from pathlib import Path
import types


def _import_build_feed(monkeypatch):
    module_name = "src.build_feed"
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.syspath_prepend(str(root / "src"))
    providers = types.ModuleType("providers")
    wl = types.ModuleType("providers.wiener_linien")
    wl.fetch_events = lambda timeout=None: []
    oebb = types.ModuleType("providers.oebb")
    oebb.fetch_events = lambda timeout=None: []
    vor = types.ModuleType("providers.vor")
    vor.fetch_events = lambda timeout=None: []
    monkeypatch.setitem(sys.modules, "providers", providers)
    monkeypatch.setitem(sys.modules, "providers.wiener_linien", wl)
    monkeypatch.setitem(sys.modules, "providers.oebb", oebb)
    monkeypatch.setitem(sys.modules, "providers.vor", vor)
    sys.modules.pop(module_name, None)
    module = importlib.import_module(module_name)
    module.refresh_from_env()
    return module


def test_slow_provider_does_not_block(monkeypatch):
    # Use 1s timeout
    monkeypatch.setenv("PROVIDER_TIMEOUT", "1")
    build_feed = _import_build_feed(monkeypatch)

    def slow_fetch(timeout=None):
        # Sleep 1.2s to trigger timeout
        time.sleep(1.2)
        return [{"guid": "slow"}]

    def fast_fetch(timeout=None):
        return [{"guid": "fast"}]

    monkeypatch.setattr(
        build_feed,
        "PROVIDERS",
        [("SLOW", slow_fetch), ("FAST", fast_fetch)],
    )
    monkeypatch.setenv("SLOW", "1")
    monkeypatch.setenv("FAST", "1")
    start = time.time()
    items = build_feed._collect_items()
    elapsed = time.time() - start

    # Should finish quickly after timeout (1s)
    # Give it some buffer
    assert elapsed < 1.5, f"_collect_items blocked for {elapsed:.2f}s"
    assert items == [{"guid": "fast"}]


def test_provider_specific_timeout_override(monkeypatch):
    monkeypatch.setenv("PROVIDER_TIMEOUT", "2")
    build_feed = _import_build_feed(monkeypatch)

    def slow_fetch(timeout=None):
        time.sleep(1.2)
        return [{"guid": "slow"}]

    slow_fetch.__name__ = "slow"

    def fast_fetch(timeout=None):
        return [{"guid": "fast"}]

    fast_fetch.__name__ = "fast"

    monkeypatch.setattr(
        build_feed,
        "PROVIDERS",
        [("SLOW", slow_fetch), ("FAST", fast_fetch)],
    )
    monkeypatch.setenv("SLOW", "1")
    monkeypatch.setenv("FAST", "1")
    # Override slow provider to 1s timeout
    monkeypatch.setenv("PROVIDER_TIMEOUT_SLOW", "1")

    # Re-refresh to pick up new env vars set AFTER import
    build_feed.refresh_from_env()

    start = time.time()
    items = build_feed._collect_items()
    elapsed = time.time() - start

    assert elapsed < 1.5
    assert items == [{"guid": "fast"}]


def test_cache_providers_run_sequentially(monkeypatch):
    build_feed = _import_build_feed(monkeypatch)

    calls = []

    def make_cache_provider(name):
        def _provider(timeout=None):
            calls.append(name)
            return [{"provider": name}]

        _provider.__name__ = f"cache_{name}"
        setattr(_provider, "_provider_cache_name", name)
        return _provider

    cache_a = make_cache_provider("wl")
    cache_b = make_cache_provider("oebb")

    monkeypatch.setattr(
        build_feed,
        "PROVIDERS",
        [
            ("WL_ENABLE", cache_a),
            ("OEBB_ENABLE", cache_b),
        ],
    )

    monkeypatch.setenv("WL_ENABLE", "1")
    monkeypatch.setenv("OEBB_ENABLE", "1")

    def fail_executor(*args, **kwargs):
        raise AssertionError("ThreadPoolExecutor should not be used for cache providers")

    monkeypatch.setattr(build_feed, "ThreadPoolExecutor", fail_executor)

    items = build_feed._collect_items()

    assert items == [{"provider": "wl"}, {"provider": "oebb"}]
    assert calls == ["wl", "oebb"]


def test_provider_worker_limit(monkeypatch):
    build_feed = _import_build_feed(monkeypatch)
    monkeypatch.setenv("PROVIDER_MAX_WORKERS", "4")
    monkeypatch.setenv("PROVIDER_MAX_WORKERS_GROUP", "1")
    build_feed.refresh_from_env()

    active = 0
    max_active = 0
    lock = threading.Lock()

    def make_fetch(name):
        def _fetch(timeout=None):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            try:
                # Sleep a tiny bit to allow overlap if concurrency was broken
                time.sleep(0.05)
            finally:
                with lock:
                    active -= 1
            return [{"name": name}]

        _fetch.__name__ = name
        _fetch._provider_concurrency_key = "group"
        return _fetch

    first = make_fetch("first")
    second = make_fetch("second")

    monkeypatch.setattr(
        build_feed,
        "PROVIDERS",
        [("FIRST", first), ("SECOND", second)],
    )
    monkeypatch.setenv("FIRST", "1")
    monkeypatch.setenv("SECOND", "1")

    items = build_feed._collect_items()

    assert len(items) == 2
    assert max_active == 1
