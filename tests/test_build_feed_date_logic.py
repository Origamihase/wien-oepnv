import importlib
import logging
import sys
import types
import datetime as dt_module
from datetime import timezone
from pathlib import Path


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


def test_format_local_times_end_before_start_future(monkeypatch, caplog):
    build_feed = _import_build_feed(monkeypatch)

    # Mock datetime.now inside build_feed to have a fixed "today"
    class MockDatetime(dt_module.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2023, 1, 1, 12, 0, tzinfo=tz)

    monkeypatch.setattr(build_feed, "datetime", MockDatetime)

    # Start date in the future
    start = MockDatetime(2023, 1, 5, 12, 0, tzinfo=timezone.utc)
    # End date before start date
    end = MockDatetime(2023, 1, 4, 12, 0, tzinfo=timezone.utc)

    with caplog.at_level(logging.WARNING):
        result = build_feed.format_local_times(start, end)

    # Since start is in the future, it should use 'Ab ...'
    assert result == "Ab 05.01.2023"

    # Verify the warning was logged
    warnings = [record.getMessage() for record in caplog.records if record.levelname == "WARNING"]
    assert "Enddatum liegt vor Startdatum" in warnings


def test_format_local_times_end_before_start_past(monkeypatch, caplog):
    build_feed = _import_build_feed(monkeypatch)

    # Mock datetime.now inside build_feed to have a fixed "today"
    class MockDatetime(dt_module.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2023, 1, 10, 12, 0, tzinfo=tz)

    monkeypatch.setattr(build_feed, "datetime", MockDatetime)

    # Start date in the past
    start = MockDatetime(2023, 1, 5, 12, 0, tzinfo=timezone.utc)
    # End date before start date
    end = MockDatetime(2023, 1, 4, 12, 0, tzinfo=timezone.utc)

    with caplog.at_level(logging.WARNING):
        result = build_feed.format_local_times(start, end)

    # Since start is in the past, it should use 'Seit ...'
    assert result == "Seit 05.01.2023"

    # Verify the warning was logged
    warnings = [record.getMessage() for record in caplog.records if record.levelname == "WARNING"]
    assert "Enddatum liegt vor Startdatum" in warnings
