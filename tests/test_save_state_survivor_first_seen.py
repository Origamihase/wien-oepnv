"""Regression test for the ``_drop_old_items`` survivor-identity guard.

Pre-fix ``_drop_old_items`` populated ``dropped`` per-item, so a
duplicate-identity pair (e.g. a duplicate-guid pair across providers or
plugins) where one item expired or aged out while the other survived
ended up with:

  * the shared identity in ``dropped`` (from the expired sibling), AND
  * the survivor returned in ``out``, which ``_make_rss`` then iterates
    and writes a fresh ``first_seen`` entry for under the same identity.

``_save_state(state, deletions=dropped)`` would then ``update`` the
survivor's freshly-written first_seen onto disk and immediately ``pop``
it (the unconditional ``for k in deletions: pop(k)`` loop). The result
was perpetual churn for the surviving disruption: every cycle it was
treated as brand-new, re-published via the fresh-pubDate window, and
the FIFO retirement gate on ``first_seen`` could not fire.

Post-fix ``_drop_old_items`` subtracts the surviving items' identities
from ``dropped`` before returning, so ``dropped`` carries only items
with no surviving twin. ``_save_state``'s existing pruning semantics
(prune every key in ``deletions``) is therefore safe.
"""
from __future__ import annotations

import importlib
import json
import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


def _import_build_feed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> types.ModuleType:
    """Mirror the setup used by ``tests/test_first_seen_cleanup.py``."""
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
    sys.modules.pop("feed", None)
    sys.modules.pop("feed.config", None)
    sys.modules.pop("src.feed", None)
    sys.modules.pop("src.feed.config", None)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STATE_PATH", "data/state_survivor.json")

    return importlib.import_module(module_name)


def test_drop_old_items_excludes_surviving_twin_from_dropped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A duplicate-identity pair (one expired, one ongoing) MUST result in
    the surviving twin keeping the identity OUT of ``dropped``. Pre-fix the
    shared identity ended up in ``dropped`` and ``_save_state`` then wiped
    the survivor's freshly-written first_seen.
    """
    build_feed = _import_build_feed(monkeypatch, tmp_path)
    now = datetime(2026, 5, 29, 12, 0, tzinfo=UTC)

    expired = {
        "guid": "guid-G",
        "_identity": "guid-G",
        "ends_at": now - timedelta(hours=24),  # well past ENDS_AT_GRACE_MINUTES
    }
    ongoing = {
        "guid": "guid-G",
        "_identity": "guid-G",
        # no ``ends_at`` — still ongoing
    }
    state: dict[str, dict[str, object]] = {}

    out, dropped = build_feed._drop_old_items([expired, ongoing], now, state)

    survivors = [it["guid"] for it in out]
    assert "guid-G" in survivors, "survivor was dropped"
    assert "guid-G" not in dropped, (
        "Survivor's shared identity must NOT be in ``dropped`` — pre-fix "
        "the expired sibling unconditionally added it, causing "
        "``_save_state`` to wipe the survivor's freshly-written first_seen."
    )


def test_drop_old_items_keeps_truly_orphaned_drop_in_dropped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression guard: a single expired item with NO surviving twin must
    still land in ``dropped`` so normal stale-state cleanup runs."""
    build_feed = _import_build_feed(monkeypatch, tmp_path)
    now = datetime(2026, 5, 29, 12, 0, tzinfo=UTC)

    expired_only = {
        "guid": "guid-X",
        "_identity": "guid-X",
        "ends_at": now - timedelta(hours=24),
    }
    state: dict[str, dict[str, object]] = {}

    out, dropped = build_feed._drop_old_items([expired_only], now, state)

    assert out == []
    assert dropped == {"guid-X"}, (
        "An orphaned expired item must still flow into ``dropped`` so "
        "``_save_state`` can prune its inherited first_seen entry."
    )


def test_end_to_end_survivor_first_seen_is_preserved_on_disk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """End-to-end: through ``_drop_old_items`` → ``_make_rss`` →
    ``_save_state``, the survivor's freshly-written first_seen must
    appear on disk.

    Pre-fix the on-disk state file ended up empty for the survivor's
    identity, treating it as brand-new every cycle.
    """
    build_feed = _import_build_feed(monkeypatch, tmp_path)
    monkeypatch.setattr(build_feed, "validate_path", lambda p, *args: p)
    now = datetime(2026, 5, 29, 12, 0, tzinfo=UTC)

    expired = {
        "guid": "guid-G",
        "_identity": "guid-G",
        "ends_at": now - timedelta(hours=24),
    }
    ongoing = {
        "guid": "guid-G",
        "_identity": "guid-G",
    }

    state = build_feed._load_state()  # empty on first run
    kept, dropped = build_feed._drop_old_items([expired, ongoing], now, state)
    build_feed._make_rss(kept, now, state, deletions=dropped)
    build_feed._save_state(state, deletions=dropped)

    on_disk = json.loads(
        (tmp_path / "data" / "state_survivor.json").read_text("utf-8")
    )
    assert "guid-G" in on_disk, (
        "Survivor's first_seen was wiped from disk — the bug is back."
    )
    assert "first_seen" in on_disk["guid-G"]
