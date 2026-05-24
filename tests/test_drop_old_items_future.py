import importlib
import sys
from datetime import datetime, timedelta, UTC
from pathlib import Path
import pytest
import types


def _import_build_feed(monkeypatch: pytest.MonkeyPatch, env: dict[str, str] | None = None) -> types.ModuleType:
    module_name = "src.build_feed"
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.syspath_prepend(str(root / "src"))
    providers = types.ModuleType("providers")
    wl = types.ModuleType("providers.wiener_linien")
    setattr(wl, "fetch_events", lambda: [])
    oebb = types.ModuleType("providers.oebb")
    setattr(oebb, "fetch_events", lambda: [])
    monkeypatch.setitem(sys.modules, "providers", providers)
    monkeypatch.setitem(sys.modules, "providers.wiener_linien", wl)
    monkeypatch.setitem(sys.modules, "providers.oebb", oebb)
    env = env or {}
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    sys.modules.pop(module_name, None)
    module = importlib.import_module(module_name)
    module.refresh_from_env()
    return module


def test_future_ends_at_skips_max_age(monkeypatch: pytest.MonkeyPatch) -> None:
    build_feed = _import_build_feed(
        monkeypatch,
        {"MAX_ITEM_AGE_DAYS": "365", "ABSOLUTE_MAX_AGE_DAYS": "540"},
    )
    now = datetime.now(UTC)
    # Aging is by first_seen (feed presence), so it is driven by state.
    future = {"title": "future", "guid": "G-future", "ends_at": now + timedelta(days=1)}
    no_end = {"title": "no_end", "guid": "G-no-end"}
    too_old = {"title": "too_old", "guid": "G-too-old", "ends_at": now + timedelta(days=1)}
    state = {
        # 400 d in the feed: past MAX (365) but a future ends_at skips the MAX drop.
        "G-future": {"first_seen": (now - timedelta(days=400)).isoformat()},
        # 400 d in the feed, no ends_at → dropped by MAX.
        "G-no-end": {"first_seen": (now - timedelta(days=400)).isoformat()},
        # 541 d in the feed → ABSOLUTE drop regardless of ends_at.
        "G-too-old": {"first_seen": (now - timedelta(days=541)).isoformat()},
    }
    res, _ = build_feed._drop_old_items([future, no_end, too_old], now, state)
    assert res == [future]


def test_first_seen_used_when_no_dates(monkeypatch: pytest.MonkeyPatch) -> None:
    build_feed = _import_build_feed(
        monkeypatch,
        {"MAX_ITEM_AGE_DAYS": "2", "ABSOLUTE_MAX_AGE_DAYS": "10"},
    )

    now = datetime.now(UTC)

    old = {"title": "old", "source": "Test", "category": "Info"}
    keep = {"title": "keep", "source": "Test", "category": "Info"}
    new_item = {"title": "new", "source": "Test", "category": "Info"}

    old_ident = build_feed._identity_for_item(old)
    keep_ident = build_feed._identity_for_item(keep)

    state = {
        old_ident: {"first_seen": (now - timedelta(days=3)).isoformat()},
        keep_ident: {"first_seen": (now - timedelta(days=1)).isoformat()},
    }

    res, _ = build_feed._drop_old_items([old, keep, new_item], now, state)
    assert res == [keep, new_item]
