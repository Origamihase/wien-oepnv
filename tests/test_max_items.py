import importlib
import sys
from datetime import datetime, timezone
from pathlib import Path


def test_max_items_non_negative(monkeypatch):
    monkeypatch.setenv("MAX_ITEMS", "-5")
    module_name = "src.build_feed"
    # Ensure 'providers' package can be found
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "src"))
    sys.modules.pop(module_name, None)
    build_feed = importlib.import_module(module_name)
    build_feed.refresh_from_env()
    # Refactored build_feed uses feed_config.MAX_ITEMS
    assert build_feed.feed_config.MAX_ITEMS == 0


def test_make_rss_ignores_items_when_max_is_zero(monkeypatch):
    module_name = "src.build_feed"
    monkeypatch.setenv("MAX_ITEMS", "0")
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "src"))
    sys.modules.pop(module_name, None)
    build_feed = importlib.import_module(module_name)
    build_feed.refresh_from_env()
    try:
        captured_state = {"value": None}

        def capture_state(state):
            captured_state["value"] = state

        monkeypatch.setattr(build_feed, "_save_state", capture_state)

        def fail_emit(*_args, **_kwargs):  # pragma: no cover - should not be called
            raise AssertionError("_emit_item should not run when MAX_ITEMS is 0")

        monkeypatch.setattr(build_feed, "_emit_item", fail_emit)

        now = datetime.now(timezone.utc)
        state = {}
        rss = build_feed._make_rss(
            [
                {
                    "source": "test",
                    "category": "cat",
                    "title": "Test",
                    "description": "Desc",
                    "link": "https://example.test",
                    "guid": "guid-1",
                    "pubDate": now,
                }
            ],
            now,
            state,
        )

        assert "<item>" not in rss
        assert state == {}
        assert captured_state["value"] == {}
    finally:
        sys.modules.pop(module_name, None)
