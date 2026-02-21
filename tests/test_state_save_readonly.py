import importlib
import sys
import types
import logging
from pathlib import Path
from datetime import datetime, timezone


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
    return importlib.import_module(module_name)


def test_make_rss_logs_warning_when_state_readonly(monkeypatch, caplog):
    build_feed = _import_build_feed(monkeypatch)

    def fail_save(_):
        raise PermissionError("read-only file system")

    monkeypatch.setattr(build_feed, "_save_state", fail_save)

    now = datetime.now(timezone.utc)
    item = {
        "source": "test",
        "category": "cat",
        "title": "L1: foo",
        "pubDate": now,
    }

    with caplog.at_level(logging.WARNING):
        rss = build_feed._make_rss([item], now, {})

    assert "</rss>" in rss
    assert any("State speichern fehlgeschlagen" in r.message for r in caplog.records)


def test_make_rss_saves_empty_state_when_no_identities(monkeypatch, caplog):
    build_feed = _import_build_feed(monkeypatch)

    captured = {"state": None}

    def marker(state, deletions=None):  # pragma: no cover - trivial
        captured["state"] = state

    monkeypatch.setattr(build_feed, "_save_state", marker)

    with caplog.at_level(logging.WARNING):
        rss = build_feed._make_rss(
            [],
            datetime.now(timezone.utc),
            {"old": {"first_seen": datetime.now(timezone.utc).isoformat()}},
        )

    assert "</rss>" in rss
    # State should be preserved when feed is empty
    assert captured["state"] != {}
    assert "old" in captured["state"]
    assert not any("State speichern fehlgeschlagen" in r.message for r in caplog.records)

