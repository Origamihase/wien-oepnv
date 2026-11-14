"""Helpers of the tests of the section "Das Jahr im Überblick" (``year_stats``).

The headings of the section, the builders of ledger rows, the readers of a
rendered table and the synthetic year with known values (with the fixture
``page``). Used by ``tests/test_year_stats.py`` and its siblings
``tests/test_year_stats_*.py``.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.markdown_stats.rows import (
    AusfallRow,
    StammstreckeRow,
    StoerungRow,
)
from src.markdown_stats.year_stats import render_year_overview


ROOT = Path(__file__).resolve().parents[1]
VIENNA = ZoneInfo("Europe/Vienna")

PERIOD = "### Erfasster Zeitraum"
MONTHS = "### Nach Monaten"
DIRECTIONS = "### Verspätungen nach Richtung"
DELAY_DAYS = "### Tage mit der höchsten mittleren Verspätung"
AUSFALL_DAYS = "### Tage mit den meisten Ausfällen"

# The weekday column of every test row is wrong on purpose: the tables must
# take the weekday from the timestamp.
WRONG_WEEKDAY = "XX"

_UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")


def _at(month: int, day: int, hour: int = 12, minute: int = 0, *, year: int = 2026) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=VIENNA)


def _sm(when: datetime, delay: float, direction: str = "Meidling") -> StammstreckeRow:
    return StammstreckeRow(when, WRONG_WEEKDAY, when.hour, direction, delay)


def _au(when: datetime, line: str, direction: str = "Meidling") -> AusfallRow:
    return AusfallRow(when, WRONG_WEEKDAY, when.hour, direction, line)


def _st(when: datetime) -> StoerungRow:
    return StoerungRow(when, WRONG_WEEKDAY, when.hour, "Wiener Linien", "unbekannt")


def _day_of_delays(
    month: int, day: int, delays: Sequence[float], direction: str = "Meidling"
) -> list[StammstreckeRow]:
    """One row per half hour from 06:00 on, each with its delay."""
    return [
        _sm(_at(month, day, 6 + i // 2, 30 * (i % 2)), delay, direction)
        for i, delay in enumerate(delays)
    ]


def _section(lines: list[str], heading: str) -> list[str]:
    """The lines of one ``###`` part, without its heading."""
    start = lines.index(heading) + 1
    end = next(
        (i for i in range(start, len(lines)) if lines[i].startswith("#")), len(lines)
    )
    return lines[start:end]


def _table(lines: list[str], heading: str) -> list[list[str]]:
    """Body rows of the table under *heading*, split at unescaped pipes."""
    rows = [
        [cell.strip() for cell in _UNESCAPED_PIPE.split(line)[1:-1]]
        for line in _section(lines, heading)
        if line.startswith("|")
    ]
    return rows[2:]


def _header(lines: list[str], heading: str) -> list[str]:
    line = next(row for row in _section(lines, heading) if row.startswith("|"))
    return [cell.strip() for cell in line.split("|")[1:-1]]


# ---- A synthetic year with known values ------------------------------------
#
# Delays (direction, day, rows × delay):
#   Meidling     Di 10.03.  12 × 2.0                    mean 2.0
#   Meidling     Mi 11.03.   9 × 1.0, 1 × 11.0          mean 2.0   (ties 10.03.)
#   Praterstern  Fr 05.06.   9 × 9.0, 1 × 30.0          mean 11.1  (9.0 is no exceedance)
#   Praterstern  Sa 06.06.   9 × 50.0                   mean 50.0  (under 10 rows)
#   Praterstern  Do 02.07.   2 × 50.0                   (same maximum as 06.06.)
#   Meidling     Fr 03.07.  10 × 0.0                    mean 0.0
# April and May have no delay row and lie between the first and the last one.
#
# Cancellations: Sa 14.02. S1 S1 S2 · Mi 20.05. S3 S2 S3 S2 ·
#   Do 21.05. S28700 S28700 S1 (train numbers count as "ohne Linienangabe").
# March and April have none and lie between the first and the last one.
#
# Disruptions: 01.01. twice, 31.12. once; every month in between is recorded.


def _synthetic_year() -> tuple[list[StammstreckeRow], list[StoerungRow], list[AusfallRow]]:
    sm = [
        *_day_of_delays(3, 10, [2.0] * 12),
        *_day_of_delays(3, 11, [1.0] * 9 + [11.0]),
        *_day_of_delays(6, 5, [9.0] * 9 + [30.0], "Praterstern"),
        *_day_of_delays(6, 6, [50.0] * 9, "Praterstern"),
        *_day_of_delays(7, 2, [50.0] * 2, "Praterstern"),
        *_day_of_delays(7, 3, [0.0] * 10),
    ]
    au = [
        *(_au(_at(2, 14, 7, i), line) for i, line in enumerate(["S1", "S1", "S2"])),
        *(_au(_at(5, 20, 7, i), line) for i, line in enumerate(["S3", "S2", "S3", "S2"])),
        *(_au(_at(5, 21, 7, i), line) for i, line in enumerate(["S28700", "S28700", "S1"])),
    ]
    st = [_st(_at(1, 1, 5)), _st(_at(1, 1, 6)), _st(_at(12, 31, 23, 59))]
    return sm, st, au


@pytest.fixture(name="page")
def _page() -> list[str]:
    sm, st, au = _synthetic_year()
    return render_year_overview(sm, st, au, year=2026)


def _half_up(value: Fraction) -> str:
    """*value* with one decimal, a half away from zero, worked out in fractions."""
    tenths = math.floor(abs(value) * 10 + Fraction(1, 2))
    sign = "-" if value < 0 and tenths else ""
    return f"{sign}{tenths // 10}.{tenths % 10} min"


def _exact(delay: float) -> Fraction:
    """The decimal number a ledger cell held, from the float it was read into."""
    return Fraction(Decimal(repr(delay)))


__all__ = [
    "AUSFALL_DAYS",
    "DELAY_DAYS",
    "DIRECTIONS",
    "MONTHS",
    "PERIOD",
    "ROOT",
    "VIENNA",
    "WRONG_WEEKDAY",
    "_at",
    "_au",
    "_day_of_delays",
    "_exact",
    "_half_up",
    "_header",
    "_page",
    "_section",
    "_sm",
    "_st",
    "_synthetic_year",
    "_table",
]
