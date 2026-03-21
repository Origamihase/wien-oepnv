import importlib
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
import types


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


def test_to_utc_converts_timezone(monkeypatch):
    build_feed = _import_build_feed(monkeypatch)
    cet = timezone(timedelta(hours=2))
    dt = datetime(2025, 1, 1, 12, 0, tzinfo=cet)
    result = build_feed._to_utc(dt)
    assert result.tzinfo == timezone.utc
    assert result.hour == 10


def test_fmt_rfc2822_outputs_vienna(monkeypatch):
    build_feed = _import_build_feed(monkeypatch)
    cet = timezone(timedelta(hours=2))
    dt = datetime(2025, 1, 1, 12, 0, tzinfo=cet)
    formatted = build_feed._fmt_rfc2822(dt)
    # 12:00 +0200 -> 10:00 UTC -> 11:00 +0100 (Vienna Winter)
    assert formatted.endswith("+0100")
    assert "11:00:00" in formatted


def test_fmt_rfc2822_fallback_locale_independent(monkeypatch):
    build_feed = _import_build_feed(monkeypatch)

    # Force the fallback by breaking format_datetime
    def broken_formatter(_):
        raise RuntimeError("simulated failure")
    monkeypatch.setattr(build_feed, "format_datetime", broken_formatter)

    # Create a specific datetime: March 1, 2023, 12:00:00 UTC
    dt = datetime(2023, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

    formatted = build_feed._fmt_rfc2822(dt)

    # 12:00 UTC -> 13:00 CET (+0100)
    # March 1, 2023 is a Wednesday ("Wed")
    # "Wed, 01 Mar 2023 13:00:00 +0100"
    assert formatted == "Wed, 01 Mar 2023 13:00:00 +0100"

    # Another test for daylight saving time (August 15, 2023)
    dt2 = datetime(2023, 8, 15, 12, 0, 0, tzinfo=timezone.utc)
    formatted2 = build_feed._fmt_rfc2822(dt2)

    # 12:00 UTC -> 14:00 CEST (+0200)
    # August 15, 2023 is a Tuesday ("Tue")
    assert formatted2 == "Tue, 15 Aug 2023 14:00:00 +0200"
