import importlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import pytest
import types


def _import_build_feed(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
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


def test_item_with_past_ends_at_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    build_feed = _import_build_feed(monkeypatch)
    now = datetime.now(timezone.utc)
    future = {"title": "future", "ends_at": now + timedelta(hours=1)}
    past = {"title": "past", "ends_at": now - timedelta(minutes=11)}

    def fake_collect(report: Any = None) -> list[dict[str, Any]]:
        return [future, past]

    captured = {}

    def fake_make_rss(items: Any, now_param: Any, state: Any, deletions: Any = None) -> str:
        captured["items"] = items
        return ""

    monkeypatch.setattr(build_feed, "_collect_items", fake_collect)
    monkeypatch.setattr(build_feed, "_make_rss", fake_make_rss)
    monkeypatch.chdir(tmp_path)
    build_feed.OUT_PATH = "docs/feed.xml"

    build_feed.main()

    assert captured["items"] == [future]

