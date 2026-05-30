"""Regression test: ``_parse_datetime`` must not crash on an out-of-range
numeric epoch (``scripts/update_baustellen_cache.py``).

The numeric branch ``datetime.fromtimestamp(float(value), tz=VIENNA_TZ)``
caught only ``(ValueError, OSError)``. A garbled Stadt-Wien WFS
``BEGINN``/``ENDE`` field carrying a huge finite number (``1e20`` /
``10**20`` — ``loads_finite`` admits any finite value) makes
``fromtimestamp`` raise ``OverflowError`` (NOT a ValueError/OSError subclass),
which propagated through ``_feature_to_event`` → ``_collect_events`` →
``main`` and crashed the entire baustellen cache update for the cycle. The
string/dateutil branch already caught ``OverflowError``; the numeric branch
was missing it.
"""

from __future__ import annotations

from datetime import datetime

from scripts.update_baustellen_cache import _parse_datetime


def test_out_of_range_numeric_returns_none_not_crash() -> None:
    for value in (10**20, 1e20, -(10**20), 1e300 * 1e300):  # last is +inf-ish
        assert _parse_datetime(value) is None, value


def test_normal_numeric_epoch_still_parses() -> None:
    parsed = _parse_datetime(1_700_000_000)
    assert isinstance(parsed, datetime)
    assert parsed.year == 2023


def test_string_and_none_paths_unaffected() -> None:
    assert _parse_datetime(None) is None
    assert _parse_datetime("  ") is None
    assert isinstance(_parse_datetime("2026-03-22Z"), datetime)
