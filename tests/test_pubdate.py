import importlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import pytest
import types
from defusedxml import ElementTree as ET


def _import_build_feed(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
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
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


# Any: build_feed is dynamically-loaded module; item/state shapes vary per test
def _emit_item_str(build_feed: Any, item: Any, now: datetime, state: dict[str, Any]) -> Any:
    ident, elem, replacements = build_feed._emit_item(item, now, state)
    xml_str = ET.tostring(elem, encoding="unicode")
    for ph, content in replacements.items():
        xml_str = xml_str.replace(ph, content)
    return ident, xml_str


def test_pubdate_added_for_fresh_item(monkeypatch: pytest.MonkeyPatch) -> None:
    build_feed = _import_build_feed(monkeypatch)
    now = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    state: dict[str, dict[str, Any]] = {}
    item = {"title": "A"}
    _, xml = _emit_item_str(build_feed, item, now, state)
    assert "<pubDate>" in xml
    assert build_feed._fmt_rfc2822(now) in xml


def test_pubdate_not_added_after_window(monkeypatch: pytest.MonkeyPatch) -> None:
    build_feed = _import_build_feed(monkeypatch)
    now = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    old = now - timedelta(minutes=build_feed.feed_config.FRESH_PUBDATE_WINDOW_MIN + 1)
    state = {"id": {"first_seen": old.isoformat()}}
    item = {"_identity": "id", "title": "A"}
    _, xml = _emit_item_str(build_feed, item, now, state)
    assert "<pubDate>" not in xml


def test_fresh_pubdate_window_min_rejects_negative_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRESH_PUBDATE_WINDOW_MIN", "-10")
    # Need to reload config to pick up the env var change
    from src.feed import config
    config.refresh_from_env()
    assert config.FRESH_PUBDATE_WINDOW_MIN == 0

    monkeypatch.setenv("FRESH_PUBDATE_WINDOW_MIN", "15")
    config.refresh_from_env()
    assert config.FRESH_PUBDATE_WINDOW_MIN == 15
