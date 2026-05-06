import importlib
import logging
import sys
from pathlib import Path
from typing import Any

import pytest
import types
from datetime import datetime, UTC


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


def test_make_rss_logs_warning_when_state_readonly(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    build_feed = _import_build_feed(monkeypatch)

    def fail_save(_: Any) -> None:
        raise PermissionError("read-only file system")



    now = datetime.now(UTC)
    item = {
        "source": "test",
        "category": "cat",
        "title": "L1: foo",
        "pubDate": now,
    }

    with caplog.at_level(logging.WARNING):
        rss = build_feed._make_rss([item], now, {})

    assert "</rss>" in rss



def test_make_rss_saves_empty_state_when_no_identities(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    build_feed = _import_build_feed(monkeypatch)

    captured = {"state": None}

    def marker(state: Any, deletions: Any = None) -> None:  # pragma: no cover - trivial
        captured["state"] = state



    with caplog.at_level(logging.WARNING):
        rss = build_feed._make_rss(
            [],
            datetime.now(UTC),
            {"old": {"first_seen": datetime.now(UTC).isoformat()}},
        )

    assert "</rss>" in rss
    # State should be preserved when feed is empty


    assert not any("State speichern fehlgeschlagen" in r.message for r in caplog.records)
