"""Regression test: a naive CSV timestamp must not crash the README patch
(``scripts/generate_markdown_stats.py``).

The three row parsers called ``datetime.fromisoformat(row["timestamp"])``,
which returns a *naive* ``datetime`` when the stored value lacks a UTC offset
(a hand-edited row, or a future writer that drops the offset). Comparing that
against the tz-aware ``cutoff`` in ``_filter_rows_by_window`` /
``_filter_rows_by_timedelta`` raised ``TypeError: can't compare offset-naive
and offset-aware datetimes`` — uncaught in ``main`` (only ``OSError`` is
caught), so ``docs/statistik.md`` was written but the README patch crashed
(exit 1) and the README was left stale.

The fix routes every row timestamp through ``_aware_fromisoformat``, which
coerces a naive value to ``Europe/Vienna`` (mirroring the ``--now-iso``
handling and the CSV writer's ``to_vienna`` convention), so genuinely
offset-carrying rows are unchanged.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from scripts.generate_markdown_stats import (
    VIENNA_TZ,
    _aware_fromisoformat,
    _filter_rows_by_timedelta,
    _filter_rows_by_window,
    _parse_stammstrecke_rows,
)

_NOW = datetime(2026, 5, 30, 12, 0, tzinfo=VIENNA_TZ)


def _naive_row() -> list[dict[str, str]]:
    # No UTC offset on the timestamp -> fromisoformat would return naive.
    return [
        {"timestamp": "2026-05-28T10:00:00", "delay_minutes": "5.0", "direction": "City"}
    ]


def test_aware_fromisoformat_coerces_naive_to_vienna() -> None:
    assert _aware_fromisoformat("2026-05-28T10:00:00").tzinfo == VIENNA_TZ


def test_aware_fromisoformat_preserves_offset() -> None:
    parsed = _aware_fromisoformat("2026-05-28T10:00:00+02:00")
    assert parsed.utcoffset() == timedelta(hours=2)


def test_parse_rows_yields_aware_timestamps() -> None:
    rows = _parse_stammstrecke_rows(_naive_row())
    assert len(rows) == 1
    assert rows[0].timestamp.tzinfo is not None


def test_window_filters_do_not_crash_on_naive_csv_row() -> None:
    """The pre-fix crash path: naive row timestamp vs aware cutoff."""
    rows = _parse_stammstrecke_rows(_naive_row())
    assert _filter_rows_by_window(rows, days=30, now=_NOW) == rows
    assert (
        _filter_rows_by_timedelta(rows, delta=timedelta(days=30), now=_NOW) == rows
    )
