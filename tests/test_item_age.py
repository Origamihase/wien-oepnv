import importlib
import sys
from pathlib import Path
from typing import Any

import pytest
import types
from datetime import datetime, timedelta, UTC


def _import_build_feed(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> types.ModuleType:
    module_name = "src.build_feed"
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.syspath_prepend(str(root / "src"))
    # Provide lightweight provider stubs to avoid heavy deps during import
    providers = types.ModuleType("providers")
    wl = types.ModuleType("providers.wiener_linien")
    setattr(wl, "fetch_events", lambda: [])
    oebb = types.ModuleType("providers.oebb")
    setattr(oebb, "fetch_events", lambda: [])
    monkeypatch.setitem(sys.modules, "providers", providers)
    monkeypatch.setitem(sys.modules, "providers.wiener_linien", wl)
    monkeypatch.setitem(sys.modules, "providers.oebb", oebb)
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


def test_main_filters_items_older_than_max(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    build_feed = _import_build_feed(
        monkeypatch,
        {"MAX_ITEM_AGE_DAYS": "2", "ABSOLUTE_MAX_AGE_DAYS": "10"},
    )

    now = datetime.now(UTC)
    # Aging is now by first_seen (feed presence), not the source date.
    recent = {"title": "recent", "guid": "G-recent"}
    old = {"title": "old", "guid": "G-old"}
    fake_state = {
        "G-recent": {"first_seen": (now - timedelta(days=1)).isoformat()},
        "G-old": {"first_seen": (now - timedelta(days=2, minutes=1)).isoformat()},
    }

    def fake_collect(report: Any = None) -> list[dict[str, Any]]:
        return [recent, old]

    captured: dict[str, Any] = {}

    def fake_make_rss(
        items: Any,
        now_param: Any,
        state: Any,
        deletions: Any = None,
        *,
        lang: str = "de",
    ) -> str:
        # Capture only on the first (German) call; the build pipeline now
        # also invokes ``_make_rss`` a second time for the EN mirror.
        captured.setdefault("items", items)
        return ""

    monkeypatch.setattr(build_feed, "_collect_items", fake_collect)
    monkeypatch.setattr(build_feed, "_make_rss", fake_make_rss)
    monkeypatch.setattr(build_feed, "_load_state", lambda: dict(fake_state))
    monkeypatch.setattr(build_feed, "_save_state", lambda *a, **k: None)
    monkeypatch.chdir(tmp_path)
    setattr(build_feed, "OUT_PATH", "docs/feed.xml")

    build_feed.main()

    assert captured["items"] == [recent]


def test_main_filters_items_older_than_absolute(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    build_feed = _import_build_feed(
        monkeypatch,
        {"MAX_ITEM_AGE_DAYS": "1000", "ABSOLUTE_MAX_AGE_DAYS": "2"},
    )

    now = datetime.now(UTC)
    # ABSOLUTE_MAX_AGE_DAYS applies to first_seen regardless of ends_at.
    within = {"title": "within_abs", "guid": "G-within"}
    too_old = {"title": "too_old", "guid": "G-too-old"}
    fake_state = {
        "G-within": {"first_seen": (now - timedelta(days=2) + timedelta(minutes=1)).isoformat()},
        "G-too-old": {"first_seen": (now - timedelta(days=2) - timedelta(minutes=1)).isoformat()},
    }

    def fake_collect(report: Any = None) -> list[dict[str, Any]]:
        return [within, too_old]

    captured: dict[str, Any] = {}

    def fake_make_rss(
        items: Any,
        now_param: Any,
        state: Any,
        deletions: Any = None,
        *,
        lang: str = "de",
    ) -> str:
        # Capture only on the first (German) call; the build pipeline now
        # also invokes ``_make_rss`` a second time for the EN mirror.
        captured.setdefault("items", items)
        return ""

    monkeypatch.setattr(build_feed, "_collect_items", fake_collect)
    monkeypatch.setattr(build_feed, "_make_rss", fake_make_rss)
    monkeypatch.setattr(build_feed, "_load_state", lambda: dict(fake_state))
    monkeypatch.setattr(build_feed, "_save_state", lambda *a, **k: None)
    monkeypatch.chdir(tmp_path)
    setattr(build_feed, "OUT_PATH", "docs/feed.xml")

    build_feed.main()

    assert captured["items"] == [within]
