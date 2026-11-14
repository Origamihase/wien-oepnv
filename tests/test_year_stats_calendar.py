"""The section "Das Jahr im Überblick": the Vienna calendar.

Year, day and weekday of a row follow Vienna time: the turn of the year,
the days of the clock change, naive timestamps.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.markdown_stats.rows import AusfallRow, StammstreckeRow
from src.markdown_stats.year_stats import render_year_overview
from tests._year_stats_support import (
    AUSFALL_DAYS,
    DELAY_DAYS,
    DIRECTIONS,
    MONTHS,
    PERIOD,
    VIENNA,
    _at,
    _au,
    _page,  # noqa: F401 - the fixture ``page``
    _sm,
    _st,
    _synthetic_year,
    _table,
)


# ---- The Vienna calendar ----------------------------------------------------


def test_rows_of_another_year_are_ignored(page: list[str]) -> None:
    sm, st, au = _synthetic_year()
    others_sm = [
        *(_sm(_at(8, 8, 6 + i, year=2025), 99.0) for i in range(12)),
        *(_sm(_at(8, 8, 6 + i, year=2027), 99.0, "Praterstern") for i in range(12)),
    ]
    others_au = [_au(_at(8, 8, 7, i, year=2025), "S7") for i in range(9)]
    others_st = [_st(_at(8, 8, year=2027))]
    assert render_year_overview(sm + others_sm, st + others_st, au + others_au, year=2026) == page
    assert render_year_overview(others_sm, others_st, others_au, year=2026) == []
    assert render_year_overview(sm, st, au, year=2030) == []


def test_midnight_of_new_year_follows_vienna_time() -> None:
    """31.12. 23:59:59 and 01.01. 00:00:00 Vienna, written with a UTC offset.

    Vienna is UTC+1 at New Year, so 22:59:59 UTC is still the old year and
    23:00:00 UTC is the new one; the UTC calendar year would put both into
    the old year.
    """
    last_of_2025 = datetime(2025, 12, 31, 22, 59, 59, tzinfo=UTC)
    first_of_2026 = datetime(2025, 12, 31, 23, 0, 0, tzinfo=UTC)
    last_of_2026 = datetime(2026, 12, 31, 22, 59, 59, tzinfo=UTC)
    first_of_2027 = datetime(2026, 12, 31, 23, 0, 0, tzinfo=UTC)
    stamps = [last_of_2025, first_of_2026, last_of_2026, first_of_2027]
    sm = [_sm(when, 3.0) for when in stamps]
    au = [_au(when, "S1") for when in stamps]
    st = [_st(when) for when in stamps]

    page = render_year_overview(sm, st, au, year=2026)
    months = _table(page, MONTHS)
    assert months[0] == ["Jänner", "1", "3.0 min", "0", "3.0 min", "1", "1"]
    assert months[11] == ["Dezember", "1", "3.0 min", "0", "3.0 min", "1", "1"]
    assert all(row[1:] == ["0", "–", "0", "–", "0", "0"] for row in months[1:11])
    assert _table(page, PERIOD) == [
        ["Stammstrecke-Beobachtungen", "01.01.2026", "31.12.2026"],
        ["Ausfälle", "01.01.2026", "31.12.2026"],
        ["Störungen", "01.01.2026", "31.12.2026"],
    ]
    assert _table(page, AUSFALL_DAYS) == [
        ["Do 01.01.2026", "1", "S1 (1)"],
        ["Do 31.12.2026", "1", "S1 (1)"],
    ]
    # The day named with a direction's highest delay is a Vienna day as well:
    # 23:00 UTC on 31.12.2025 is Thursday 01.01.2026, not Wednesday 31.12.2025.
    assert _table(page, DIRECTIONS) == [
        ["Meidling", "2", "3.0 min", "0", "3.0 min (Do 01.01.2026)"]
    ]

    assert _table(render_year_overview(sm, st, au, year=2025), PERIOD)[0][1:] == ["31.12.2025"] * 2
    assert _table(render_year_overview(sm, st, au, year=2027), PERIOD)[0][1:] == ["01.01.2027"] * 2


@pytest.mark.parametrize(
    ("before", "label", "after", "start_utc", "hours"),
    [
        # 25.10.2026 00:00 CEST = 24.10. 22:00 UTC; the day has 25 hours.
        ("Sa 24.10.2026", "So 25.10.2026", "Mo 26.10.2026", datetime(2026, 10, 24, 22, 0, tzinfo=UTC), 25),
        # 29.03.2026 00:00 CET = 28.03. 23:00 UTC; the day has 23 hours.
        ("Sa 28.03.2026", "So 29.03.2026", "Mo 30.03.2026", datetime(2026, 3, 28, 23, 0, tzinfo=UTC), 23),
    ],
)
def test_clock_change_day_counts_all_its_hours(
    before: str, label: str, after: str, start_utc: datetime, hours: int
) -> None:
    """One row per hour over three days, in UTC: the middle day gets its real length.

    A count by UTC date, or by 24 hours from midnight, would give the day
    of the clock change 24 rows and move one to or from its neighbour.
    """
    assert start_utc.astimezone(VIENNA).hour == 0
    first = start_utc - timedelta(hours=24)
    stamps = [first + timedelta(hours=i) for i in range(24 + hours + 24)]
    middle = stamps[24 : 24 + hours]
    sm = [_sm(when, 8.0 if when in middle else 1.0) for when in stamps]
    au = [_au(when, "S1") for when in stamps]

    page = render_year_overview(sm, [], au, year=2026)
    assert _table(page, DELAY_DAYS) == [
        [label, "8.0 min", str(hours), "8.0 min"],
        [before, "1.0 min", "24", "1.0 min"],
        [after, "1.0 min", "24", "1.0 min"],
    ]
    # Most cancellations first: the long day leads, the short day comes last.
    lengths = {before: 24, label: hours, after: 24}
    order = sorted(lengths, key=lambda day: (-lengths[day], [before, label, after].index(day)))
    assert order == ([label, before, after] if hours == 25 else [before, after, label])
    assert _table(page, AUSFALL_DAYS) == [
        [day, str(lengths[day]), f"S1 ({lengths[day]})"] for day in order
    ]


def test_weekday_comes_from_the_timestamp_not_from_the_column() -> None:
    rows = [
        StammstreckeRow(_at(11, 9, 6 + i), "Fr", 6 + i, "Meidling", 2.0) for i in range(10)
    ]
    au = [AusfallRow(_at(11, 9, 7), "Fr", 7, "Meidling", "S1")]
    page = render_year_overview(rows, [], au, year=2026)
    # 09.11.2026 is a Monday.
    assert _table(page, DELAY_DAYS)[0][0] == "Mo 09.11.2026"
    assert _table(page, AUSFALL_DAYS)[0][0] == "Mo 09.11.2026"
    assert _table(page, DIRECTIONS)[0][4] == "2.0 min (Mo 09.11.2026)"


def test_naive_timestamp_counts_as_vienna_time() -> None:
    """As ``_aware_fromisoformat`` reads a ledger row without an offset."""
    naive = datetime(2026, 12, 31, 23, 30)
    page = render_year_overview([_sm(naive, 1.0)], [], [], year=2026)
    assert _table(page, PERIOD)[0] == ["Stammstrecke-Beobachtungen", "31.12.2026", "31.12.2026"]
