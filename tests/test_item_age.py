import importlib
import sys
from pathlib import Path
import types
from datetime import datetime, timedelta, timezone


def _import_build_feed(monkeypatch, env):
    module_name = "src.build_feed"
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.syspath_prepend(str(root / "src"))
    # Provide lightweight provider stubs to avoid heavy deps during import
    providers = types.ModuleType("providers")
    wl = types.ModuleType("providers.wiener_linien")
    wl.fetch_events = lambda: []
    oebb = types.ModuleType("providers.oebb")
    oebb.fetch_events = lambda: []
    monkeypatch.setitem(sys.modules, "providers", providers)
    monkeypatch.setitem(sys.modules, "providers.wiener_linien", wl)
    monkeypatch.setitem(sys.modules, "providers.oebb", oebb)
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


def test_main_filters_items_older_than_max(monkeypatch, tmp_path):
    build_feed = _import_build_feed(
        monkeypatch,
        {"MAX_ITEM_AGE_DAYS": "2", "ABSOLUTE_MAX_AGE_DAYS": "10"},
    )

    now = datetime.now(timezone.utc)
    recent = {"title": "recent", "pubDate": now - timedelta(days=2) + timedelta(minutes=1)}
    old = {"title": "old", "pubDate": now - timedelta(days=2) - timedelta(minutes=1)}

    def fake_collect():
        return [recent, old]

    captured = {}

    def fake_make_rss(items, now_param, state):
        captured["items"] = items
        return ""

    monkeypatch.setattr(build_feed, "_collect_items", fake_collect)
    monkeypatch.setattr(build_feed, "_make_rss", fake_make_rss)
    build_feed.OUT_PATH = str(tmp_path / "feed.xml")

    build_feed.main()

    assert captured["items"] == [recent]


def test_main_filters_items_older_than_absolute(monkeypatch, tmp_path):
    build_feed = _import_build_feed(
        monkeypatch,
        {"MAX_ITEM_AGE_DAYS": "1000", "ABSOLUTE_MAX_AGE_DAYS": "2"},
    )

    now = datetime.now(timezone.utc)
    within = {
        "title": "within_abs",
        "starts_at": now - timedelta(days=2) + timedelta(minutes=1),
    }
    too_old = {
        "title": "too_old",
        "starts_at": now - timedelta(days=2) - timedelta(minutes=1),
    }

    def fake_collect():
        return [within, too_old]

    captured = {}

    def fake_make_rss(items, now_param, state):
        captured["items"] = items
        return ""

    monkeypatch.setattr(build_feed, "_collect_items", fake_collect)
    monkeypatch.setattr(build_feed, "_make_rss", fake_make_rss)
    build_feed.OUT_PATH = str(tmp_path / "feed.xml")

    build_feed.main()

    assert captured["items"] == [within]

