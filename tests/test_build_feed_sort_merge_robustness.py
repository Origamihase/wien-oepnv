"""Project-wide review follow-ups:

#2 (sort): _recency_sort_key used the raw pubDate timestamp as a tiebreaker,
   so an item with a bogus FUTURE pubDate ranked ahead of genuinely-current
   items (within a first_seen + category tie). The pubDate is now clamped to
   ``now`` for the tiebreaker.

#3 (robustness): in _drain_completed_futures the ``merge_result`` call ran
   OUTSIDE the ``future.result()`` try, so an exception while merging one
   provider's result propagated out of the drain loop and lost every
   already-collected item from the other providers. The merge is now isolated
   per provider.
"""
from __future__ import annotations

import importlib
import sys
import types
from concurrent.futures import Future
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest


def _import_build_feed(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    module_name = "src.build_feed"
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.syspath_prepend(str(root / "src"))
    providers = types.ModuleType("providers")
    for name in ("wiener_linien", "oebb", "vor"):
        mod = types.ModuleType(f"providers.{name}")
        setattr(mod, "fetch_events", lambda: [])
        monkeypatch.setitem(sys.modules, f"providers.{name}", mod)
    monkeypatch.setitem(sys.modules, "providers", providers)
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


def test_recency_sort_clamps_future_pubdate(monkeypatch: pytest.MonkeyPatch) -> None:
    build_feed = _import_build_feed(monkeypatch)
    now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    future = (now + timedelta(days=200)).isoformat()
    past = (now - timedelta(days=1)).isoformat()

    k_future = build_feed._recency_sort_key({"pubDate": future, "guid": "f"}, {}, now)
    k_now = build_feed._recency_sort_key({"pubDate": now.isoformat(), "guid": "n"}, {}, now)
    k_past = build_feed._recency_sort_key({"pubDate": past, "guid": "p"}, {}, now)

    # The future pubDate is clamped to now, so its pubDate tiebreaker (index 2,
    # ``-pub_ts``) equals the current item's instead of ranking ahead of it.
    assert k_future[2] == k_now[2]
    # A genuine past pubDate still sorts AFTER now (a larger ``-pub_ts``).
    assert k_past[2] > k_now[2]


class _FakeReport:
    def __init__(self) -> None:
        self.errors: list[tuple[str, str]] = []

    def provider_error(self, name: str, msg: str) -> None:
        self.errors.append((name, msg))

    def __getattr__(self, _name: str) -> Any:
        return lambda *a, **k: None


def test_drain_isolates_a_failing_merge(monkeypatch: pytest.MonkeyPatch) -> None:
    build_feed = _import_build_feed(monkeypatch)

    fut: Future[Any] = Future()
    fut.set_result([{"title": "x"}])

    def fetch() -> list[Any]:
        return []

    fetch.__name__ = "wl_fetch"

    futures = {fut: (fetch, "Wiener Linien", 10)}
    deadlines: dict[Any, float | None] = {fut: None}
    pending: set[Any] = {fut}
    report = _FakeReport()

    def merge_result(_fetch: Any, _result: Any, _name: str) -> None:
        raise ValueError("boom")

    # Must NOT raise — the merge failure is isolated to the one provider.
    build_feed._drain_completed_futures(futures, deadlines, pending, report, merge_result)

    assert pending == set()
    assert any(name == "Wiener Linien" for name, _ in report.errors)
