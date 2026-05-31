import importlib
import logging
import sys
from typing import Any
import pytest
import types
import datetime as dt_module
from datetime import datetime
from pathlib import Path


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


def test_format_local_times_end_before_start_future(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    build_feed = _import_build_feed(monkeypatch)

    # Mock datetime.now inside build_feed to have a fixed "today"
    class MockDatetime(dt_module.datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
            return cls(2023, 1, 1, 12, 0, tzinfo=tz)

    monkeypatch.setattr(build_feed, "datetime", MockDatetime)

    # Start date in the future
    start = MockDatetime(2023, 1, 5, 12, 0, tzinfo=dt_module.UTC)
    # End date before start date
    end = MockDatetime(2023, 1, 4, 12, 0, tzinfo=dt_module.UTC)

    with caplog.at_level(logging.WARNING):
        result = build_feed.format_local_times(start, end)

    # Since start is in the future, it should use 'Ab ...'
    assert result == "Ab 05.01.2023"

    # Verify the warning was logged
    warnings = [record.getMessage() for record in caplog.records if record.levelname == "WARNING"]
    assert "Enddatum liegt vor Startdatum" in warnings


def test_format_local_times_end_before_start_past(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    build_feed = _import_build_feed(monkeypatch)

    # Mock datetime.now inside build_feed to have a fixed "today"
    class MockDatetime(dt_module.datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
            return cls(2023, 1, 10, 12, 0, tzinfo=tz)

    monkeypatch.setattr(build_feed, "datetime", MockDatetime)

    # Start date in the past
    start = MockDatetime(2023, 1, 5, 12, 0, tzinfo=dt_module.UTC)
    # End date before start date
    end = MockDatetime(2023, 1, 4, 12, 0, tzinfo=dt_module.UTC)

    with caplog.at_level(logging.WARNING):
        result = build_feed.format_local_times(start, end)

    # Since start is in the past, it should use 'Seit ...'
    assert result == "Seit 05.01.2023"

    # Verify the warning was logged
    warnings = [record.getMessage() for record in caplog.records if record.levelname == "WARNING"]
    assert "Enddatum liegt vor Startdatum" in warnings


def test_format_local_times_long_range_keeps_end(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    build_feed = _import_build_feed(monkeypatch)

    class MockDatetime(dt_module.datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
            return cls(2026, 6, 1, 12, 0, tzinfo=tz)

    monkeypatch.setattr(build_feed, "datetime", MockDatetime)

    # ~274-day span: longer than the old hard-coded 180-day cap but well
    # within ABSOLUTE_MAX_AGE_DAYS (540), so the explicit end date must
    # survive and render as a range instead of collapsing to "Seit …".
    start = MockDatetime(2026, 1, 1, 12, 0, tzinfo=dt_module.UTC)
    end = MockDatetime(2026, 10, 2, 12, 0, tzinfo=dt_module.UTC)

    with caplog.at_level(logging.WARNING):
        result = build_feed.format_local_times(start, end)

    # Renders as a range (start … end), not the single-date "Seit …" form,
    # so the explicit end date is preserved. (The separator between the
    # dates uses narrow no-break spaces around an en-dash, so assert on the
    # two date boundaries rather than the exact glyphs.)
    assert result.startswith("01.01.2026")
    assert result.endswith("02.10.2026")
    assert not result.startswith("Seit")
    warnings = [record.getMessage() for record in caplog.records if record.levelname == "WARNING"]
    assert not any("Setze Enddatum auf None" in message for message in warnings)
