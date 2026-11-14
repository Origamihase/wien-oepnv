"""A time stamp whose year lies outside 2000 to 2100 counts as not set.

Operator decision of 2026-11-10 (``src.utils.vienna_clock.PLAUSIBLE_YEARS``).
Source systems write a year at the edge of the calendar (9999, 1) for "no
end" or "unknown". Python cannot add to such a date or move it to another
time zone, and one such value ended the feed build or a whole fetch. The
parsers listed in ``READERS`` return "not set" for it. The build reads its
own state file the same way (``feed_build.base._state_stamp``).

Three groups:

* ``plausible_moment`` itself, with both boundaries;
* one list of edge values and one list of parsers (``READERS``): every
  listed parser gives "not set" for every edge value and still reads an
  ordinary one. ``test_every_function_that_applies_the_rule_is_listed``
  compares the list with the functions under ``src/`` that call
  ``plausible_moment`` by that name: a function that calls it and is not
  listed fails the test, and so does a listed function that does not call
  it. A parser that does not call it is not found that way, nor is a call
  under another name;
* each parser on its own: every return path, and ordinary values of 2026
  with the result written out.

The way from the WL fetch to the feed is pinned in
``tests/test_plausible_years_chain.py``; the dates written in a WL text or
an ÖBB description as the fetch sees them, the Stammstrecke monitor and
``lint`` in ``tests/test_plausible_years_text_dates.py``; the hours next to
a boundary, where the year depends on the time zone, in
``tests/test_plausible_years_time_zone.py``; the state file, from
``_state_stamp`` to the feed, in ``tests/test_plausible_years_state.py``.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from src.feed_build.base import _parse_datetime as parse_item_date
from src.feed_build.base import _state_stamp
from src.providers.baustellen_events import _parse_datetime as parse_wfs_date
from src.providers.oebb import _parse_dt_rfc2822, _parse_period
from src.providers.wl_item_fields import _iso
from src.providers.wl_resolved import _moment
from src.providers.wl_text import extract_date_from_title as title_date
from src.providers.wl_text import extract_end_from_description as period_end
from src.providers.wl_text import extract_start_from_description as period_start
from src.stammstrecke.legs import _parse_vao_dt
from src.utils import vienna_clock

REPO_ROOT = Path(__file__).resolve().parents[1]
VIENNA = ZoneInfo("Europe/Vienna")
CET = timezone(timedelta(hours=1))
CEST = timezone(timedelta(hours=2))
WEST = timezone(timedelta(hours=-1))
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_MONTHS_DE = (
    "Jänner", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember",
)


def _exactly(result: object, expected: datetime) -> None:
    """*result* is the moment *expected* with the same offset (same wall clock)."""
    assert isinstance(result, datetime)
    assert result == expected
    assert result.utcoffset() == expected.utcoffset()


# --- plausible_moment -----------------------------------------------------------


def test_the_plausible_years_are_2000_to_2100() -> None:
    assert vienna_clock.PLAUSIBLE_YEARS == (2000, 2100)


def test_no_moment_is_no_moment() -> None:
    assert vienna_clock.plausible_moment(None) is None


@pytest.mark.parametrize("zone", [None, UTC, CET, WEST, VIENNA], ids=["naive", "utc", "+0100", "-0100", "vienna"])
@pytest.mark.parametrize("year", [2000, 2001, 2026, 2099, 2100])
def test_a_plausible_year_gives_the_same_object(year: int, zone: Any) -> None:
    moment = datetime(year, 6, 15, 12, 30, tzinfo=zone)
    assert vienna_clock.plausible_moment(moment) is moment


@pytest.mark.parametrize("zone", [None, UTC, CET, WEST, VIENNA], ids=["naive", "utc", "+0100", "-0100", "vienna"])
@pytest.mark.parametrize("year", [1, 1970, 1999, 2101, 9999])
def test_another_year_is_not_set(year: int, zone: Any) -> None:
    assert vienna_clock.plausible_moment(datetime(year, 6, 15, 12, 30, tzinfo=zone)) is None


@pytest.mark.parametrize("zone", [None, CET], ids=["naive", "+0100"])
def test_both_boundaries_are_inclusive(zone: Any) -> None:
    first = datetime(2000, 1, 1, 0, 0, 0, tzinfo=zone)
    last = datetime(2100, 12, 31, 23, 59, 59, 999999, tzinfo=zone)
    assert vienna_clock.plausible_moment(first) is first
    assert vienna_clock.plausible_moment(last) is last
    assert vienna_clock.plausible_moment(first - timedelta(microseconds=1)) is None
    assert vienna_clock.plausible_moment(last + timedelta(microseconds=1)) is None


def test_the_year_is_read_as_written() -> None:
    # 00:30 +0100 on New Year's Day 2000 is still 1999 in UTC, and 23:30
    # -0100 on New Year's Eve 1999 is already 2000 there: the written year counts.
    written_2000 = datetime(2000, 1, 1, 0, 30, tzinfo=CET)
    written_1999 = datetime(1999, 12, 31, 23, 30, tzinfo=WEST)
    written_2100 = datetime(2100, 12, 31, 23, 30, tzinfo=WEST)
    written_2101 = datetime(2101, 1, 1, 0, 30, tzinfo=CET)
    assert vienna_clock.plausible_moment(written_2000) is written_2000
    assert vienna_clock.plausible_moment(written_1999) is None
    assert vienna_clock.plausible_moment(written_2100) is written_2100
    assert vienna_clock.plausible_moment(written_2101) is None


@pytest.mark.parametrize(
    "moment",
    [
        datetime.min,
        datetime.max,
        datetime.min.replace(tzinfo=UTC),
        datetime.max.replace(tzinfo=UTC),
        # Neither can be moved to UTC: the check does not try.
        datetime(1, 1, 1, tzinfo=CET),
        datetime(9999, 12, 31, 23, 59, 59, tzinfo=WEST),
        datetime(1, 1, 1, tzinfo=VIENNA),
        datetime(9999, 12, 31, 23, 59, 59, tzinfo=VIENNA),
    ],
)
def test_the_ends_of_the_calendar_are_not_set(moment: datetime) -> None:
    assert vienna_clock.plausible_moment(moment) is None


# --- One list of edge values, one list of parsers -------------------------------


@dataclass(frozen=True)
class Stamp:
    """A wall clock with an offset, written the way each source writes it."""

    year: int
    month: int
    day: int
    hour: int = 0
    minute: int = 0
    second: int = 0
    offset_hours: int = 1

    @property
    def _date(self) -> str:
        return f"{self.year:04d}-{self.month:02d}-{self.day:02d}"

    @property
    def _clock(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d}:{self.second:02d}"

    def _offset(self, colon: str) -> str:
        sign = "-" if self.offset_hours < 0 else "+"
        return f"{sign}{abs(self.offset_hours):02d}{colon}00"

    def wl(self) -> str:
        """Wiener Linien: ``9999-12-31T23:59:59.000+0100``."""
        return f"{self._date}T{self._clock}.000{self._offset('')}"

    def iso(self) -> str:
        """``datetime.isoformat``, as the caches hold it: ``9999-12-31T23:59:59+01:00``."""
        return f"{self._date}T{self._clock}{self._offset(':')}"

    def naive_iso(self) -> str:
        return f"{self._date}T{self._clock}"

    def rfc2822(self) -> str:
        """ÖBB ``pubDate``: ``31 Dec 9999 23:59:59 +0100``."""
        return f"{self.day:02d} {_MONTHS[self.month - 1]} {self.year:04d} {self._clock} {self._offset('')}"

    def oebb_day(self) -> str:
        """The day in an ÖBB period prefix and in a WL title or text: ``31.12.9999``."""
        return f"{self.day:02d}.{self.month:02d}.{self.year:04d}"

    def named_day(self) -> str:
        """The day as WL spells it out: ``31. Dezember 9999``."""
        return f"{self.day}. {_MONTHS_DE[self.month - 1]} {self.year:04d}"

    def vao(self) -> tuple[str, str]:
        """The date and the time of a VAO departure: ``("9999-12-31", "23:59:59")``."""
        return self._date, self._clock

    def wfs_day(self) -> str:
        """Stadt Wien WFS date: ``9999-12-31Z``."""
        return f"{self._date}Z"

    def moment(self) -> datetime:
        zone = timezone(timedelta(hours=self.offset_hours))
        return datetime(self.year, self.month, self.day, self.hour, self.minute, self.second, tzinfo=zone)

    def naive_moment(self) -> datetime:
        return self.moment().replace(tzinfo=None)

    def epoch(self) -> float:
        return self.moment().timestamp()

    def __str__(self) -> str:
        return self.iso()


# Every edge value. The first three are those of the replay of 2026-11-10.
EDGE_STAMPS: tuple[Stamp, ...] = (
    Stamp(9999, 12, 31, 23, 59, 59),
    Stamp(1, 1, 1),
    Stamp(1, 1, 1, offset_hours=-1),
    Stamp(2101, 1, 1),
    Stamp(1999, 12, 31, 23, 59, 59),
)
# What every parser still reads: an ordinary day and both boundaries.
READABLE_STAMPS: tuple[Stamp, ...] = (
    Stamp(2026, 11, 10, 8, 15),
    Stamp(2000, 1, 1),
    Stamp(2100, 12, 31, 23, 59, 59),
)


@dataclass(frozen=True)
class Reader:
    """One way a time stamp of a source or of the state file enters the code."""

    module: str
    function: str
    form: str
    read: Callable[[Stamp], object]

    def __str__(self) -> str:
        return f"{self.function} ({self.form})"


_WL_TITLE = "src/providers/wl_title_dates.py"
_WL_PERIOD = "src/providers/wl_period.py"

# Every function under src/ that applies the rule, in every form of input it
# accepts. A function that applies it and is missing here fails
# ``test_every_function_that_applies_the_rule_is_listed``.
READERS: tuple[Reader, ...] = (
    Reader("src/providers/wl_item_fields.py", "_iso", "WL stamp", lambda s: _iso(s.wl())),
    Reader("src/providers/wl_item_fields.py", "_iso", "ISO string", lambda s: _iso(s.iso())),
    Reader("src/providers/wl_item_fields.py", "_iso", "naive string", lambda s: _iso(s.naive_iso())),
    Reader("src/providers/wl_resolved.py", "_moment", "WL stamp", lambda s: _moment(s.wl())),
    Reader("src/providers/wl_resolved.py", "_moment", "ISO string", lambda s: _moment(s.iso())),
    Reader("src/feed_build/base.py", "_parse_datetime", "ISO string", lambda s: parse_item_date(s.iso())),
    Reader("src/feed_build/base.py", "_parse_datetime", "WL stamp", lambda s: parse_item_date(s.wl())),
    Reader("src/feed_build/base.py", "_parse_datetime", "naive string", lambda s: parse_item_date(s.naive_iso())),
    Reader("src/feed_build/base.py", "_parse_datetime", "datetime", lambda s: parse_item_date(s.moment())),
    Reader("src/feed_build/base.py", "_parse_datetime", "naive datetime", lambda s: parse_item_date(s.naive_moment())),
    Reader("src/feed_build/base.py", "_state_stamp", "ISO string", lambda s: _state_stamp(s.iso())),
    Reader("src/feed_build/base.py", "_state_stamp", "naive string", lambda s: _state_stamp(s.naive_iso())),
    Reader("src/feed_build/base.py", "_state_stamp", "WL stamp", lambda s: _state_stamp(s.wl())),
    Reader("src/providers/oebb.py", "_parse_dt_rfc2822", "RFC 2822", lambda s: _parse_dt_rfc2822(s.rfc2822())),
    Reader("src/providers/oebb.py", "_parse_period", "one day", lambda s: _parse_period(f"{s.oebb_day()}<br/>Text")),
    Reader(
        "src/providers/oebb.py", "_parse_period", "one day to itself",
        lambda s: _parse_period(f"{s.oebb_day()} - {s.oebb_day()}<br/>Text"),
    ),
    Reader("src/providers/baustellen_events.py", "_parse_datetime", "date with Z", lambda s: parse_wfs_date(s.wfs_day())),
    Reader("src/providers/baustellen_events.py", "_parse_datetime", "ISO string", lambda s: parse_wfs_date(s.iso())),
    Reader("src/providers/baustellen_events.py", "_parse_datetime", "naive string", lambda s: parse_wfs_date(s.naive_iso())),
    Reader("src/providers/baustellen_events.py", "_parse_datetime", "epoch number", lambda s: parse_wfs_date(s.epoch())),
    Reader(_WL_TITLE, "extract_date_from_title", "day in digits", lambda s: title_date(f"U1: Umleitung ab {s.oebb_day()}")),
    Reader(_WL_TITLE, "extract_date_from_title", "month by name", lambda s: title_date(f"U1: Umleitung ab {s.named_day()}")),
    Reader(_WL_PERIOD, "extract_start_from_description", "day in digits", lambda s: period_start(f"<p>Zeitraum: Ab {s.oebb_day()}</p>")),
    Reader(_WL_PERIOD, "extract_start_from_description", "month by name", lambda s: period_start(f"<p>Zeitraum: Ab {s.named_day()}</p>")),
    Reader(_WL_PERIOD, "extract_end_from_description", "day in digits", lambda s: period_end(f"<p>Zeitraum: bis {s.oebb_day()}</p>")),
    Reader(_WL_PERIOD, "extract_end_from_description", "month by name", lambda s: period_end(f"<p>Zeitraum: bis {s.named_day()}</p>")),
    Reader(_WL_PERIOD, "extract_end_from_description", "end of the year", lambda s: period_end(f"<p>Zeitraum: bis Ende {s.year:04d}</p>")),
    Reader("src/stammstrecke/legs.py", "_parse_vao_dt", "date and time", lambda s: _parse_vao_dt(*s.vao())),
)

# An RFC 2822 date cannot name the year 1: Python's ``email.utils`` reads a
# year below 100 as one of 1969 to 2068, also when it is written "0001"
# (``test_rfc_2822_year_below_100_is_read_as_a_year_of_our_time``).
_CANNOT_BE_WRITTEN = {("_parse_dt_rfc2822", 1)}


def _pairs(stamps: tuple[Stamp, ...]) -> list[Any]:
    return [
        pytest.param(reader, stamp, id=f"{reader} / {stamp}")
        for reader in READERS
        for stamp in stamps
        if (reader.function, stamp.year) not in _CANNOT_BE_WRITTEN
    ]


def _moments(result: object) -> list[object]:
    """The parts of a parser's result: one moment, or the two of a period."""
    return list(result) if isinstance(result, tuple) else [result]


@pytest.mark.parametrize(("reader", "stamp"), _pairs(EDGE_STAMPS))
def test_an_edge_stamp_is_not_set(reader: Reader, stamp: Stamp) -> None:
    assert all(moment is None for moment in _moments(reader.read(stamp)))


@pytest.mark.parametrize(("reader", "stamp"), _pairs(READABLE_STAMPS))
def test_a_plausible_stamp_is_read(reader: Reader, stamp: Stamp) -> None:
    moments = _moments(reader.read(stamp))
    assert all(isinstance(moment, datetime) and moment.year == stamp.year for moment in moments)


def _functions_calling_plausible_moment() -> set[tuple[str, str]]:
    """``(file, function)`` of every function under src/ that calls the name ``plausible_moment``."""
    found: set[tuple[str, str]] = set()
    for path in sorted((REPO_ROOT / "src").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "plausible_moment" not in source:
            continue
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.FunctionDef):
                continue
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                name = getattr(call.func, "id", None) or getattr(call.func, "attr", None)
                if name == "plausible_moment":
                    found.add((path.relative_to(REPO_ROOT).as_posix(), node.name))
    return found


def test_every_function_that_applies_the_rule_is_listed() -> None:
    """The functions that call ``plausible_moment`` are exactly those in ``READERS``.

    Nothing more is checked: a parser that reads a date and does not call
    ``plausible_moment``, or calls it under another name, passes unseen.
    """
    assert _functions_calling_plausible_moment() == {(reader.module, reader.function) for reader in READERS}


def test_rfc_2822_year_below_100_is_read_as_a_year_of_our_time() -> None:
    # "0001" is 2001 to ``email.utils``; no year 1 comes out of the parser.
    _exactly(_parse_dt_rfc2822("Mon, 01 Jan 0001 00:00:00 +0100"), datetime(2001, 1, 1, tzinfo=CET))
    _exactly(_parse_dt_rfc2822("01 Jan 0001 00:00:00 -0100"), datetime(2001, 1, 1, tzinfo=WEST))
    _exactly(_parse_dt_rfc2822("01 Jan 68 00:00:00 +0100"), datetime(2068, 1, 1, tzinfo=CET))
    # "99" is 1999, outside the plausible years.
    assert _parse_dt_rfc2822("01 Jan 99 00:00:00 +0100") is None


# --- Wiener Linien: wl_item_fields._iso and wl_resolved._moment -----------------

_WL_AWARE = [
    ("2026-11-10T08:15:00.000+0100", datetime(2026, 11, 10, 8, 15, tzinfo=CET)),
    ("2026-11-10T08:15:00+01:00", datetime(2026, 11, 10, 8, 15, tzinfo=CET)),
    ("2026-11-10T07:15:00Z", datetime(2026, 11, 10, 7, 15, tzinfo=UTC)),
    ("2026-07-11T09:30:00.000+0200", datetime(2026, 7, 11, 9, 30, tzinfo=CEST)),
    # A summer time with the winter offset keeps its wall clock (``vienna_wall_clock``).
    ("2026-07-11T00:00:00.000+0100", datetime(2026, 7, 11, 0, 0, tzinfo=CEST)),
    ("2027-06-30T11:11:00.000+0100", datetime(2027, 6, 30, 11, 11, tzinfo=CEST)),
    ("2000-01-01T00:00:00.000+0100", datetime(2000, 1, 1, tzinfo=CET)),
    ("2100-12-31T23:59:59.000+0100", datetime(2100, 12, 31, 23, 59, 59, tzinfo=CET)),
]
_WL_NOT_SET = [
    "9999-12-31T23:59:59.000+0100",
    "9999-12-31T23:59:59.000-0100",
    "9999-12-31T23:59:59Z",
    "0001-01-01T00:00:00.000+0100",
    "0001-01-01T00:00:00.000-0100",
    "0001-01-01T00:00:00Z",
    "2101-01-01T00:00:00.000+0100",
    "1999-12-31T23:59:59.000+0100",
    # Outside what a datetime can hold, or no date at all.
    "0000-01-01T00:00:00.000+0100",
    "10000-01-01T00:00:00.000+0100",
    "kein Datum",
    "",
]
_NO_STRING = [None, 0, 1794298500, 253402297199.0, ["2026-11-10T08:15:00Z"], {"start": "2026-11-10"}, True]


@pytest.mark.parametrize(("text", "expected"), _WL_AWARE)
def test_wl_parsers_read_an_ordinary_stamp_as_before(text: str, expected: datetime) -> None:
    _exactly(_iso(text), expected)
    _exactly(_moment(text), expected)


@pytest.mark.parametrize("text", _WL_NOT_SET)
def test_wl_parsers_give_nothing_for_an_edge_or_unreadable_stamp(text: str) -> None:
    assert _iso(text) is None
    assert _moment(text) is None


@pytest.mark.parametrize("value", _NO_STRING)
def test_wl_parsers_give_nothing_for_a_value_that_is_no_string(value: object) -> None:
    assert _iso(value) is None
    assert _moment(value) is None


def test_wl_stamp_without_offset() -> None:
    # ``_iso`` reads it as UTC; ``_moment`` wants an offset. An edge year is
    # not set in both.
    _exactly(_iso("2026-11-10T08:15:00"), datetime(2026, 11, 10, 8, 15, tzinfo=UTC))
    assert _moment("2026-11-10T08:15:00") is None
    for text in ("9999-12-31T23:59:59", "0001-01-01T00:00:00"):
        assert _iso(text) is None
        assert _moment(text) is None


# --- Feed chain: feed_build.base._parse_datetime --------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-11-10T08:15:00+01:00", datetime(2026, 11, 10, 8, 15, tzinfo=CET)),
        ("2026-11-10T08:15:00.000+0100", datetime(2026, 11, 10, 8, 15, tzinfo=CET)),
        ("  2026-11-10T08:15:00+01:00  ", datetime(2026, 11, 10, 8, 15, tzinfo=CET)),
        ("2026-11-10T07:15:00Z", datetime(2026, 11, 10, 7, 15, tzinfo=UTC)),
        # No correction of the offset here: the value is taken as given.
        ("2026-07-11T00:00:00+01:00", datetime(2026, 7, 11, 0, 0, tzinfo=CET)),
        # A string without offset is Vienna time.
        ("2026-11-10T08:15:00", datetime(2026, 11, 10, 8, 15, tzinfo=VIENNA)),
        ("2026-07-11T09:30:00", datetime(2026, 7, 11, 9, 30, tzinfo=VIENNA)),
        ("2026-11-10", datetime(2026, 11, 10, tzinfo=VIENNA)),
        # Midnight written as 24:00 is 00:00 of the next day.
        ("2026-11-10T24:00:00+01:00", datetime(2026, 11, 11, tzinfo=CET)),
        ("2000-01-01T00:00:00+01:00", datetime(2000, 1, 1, tzinfo=CET)),
        ("2100-12-31T23:59:59+01:00", datetime(2100, 12, 31, 23, 59, 59, tzinfo=CET)),
    ],
)
def test_item_date_string_is_read_as_before(text: str, expected: datetime) -> None:
    _exactly(parse_item_date(text), expected)


@pytest.mark.parametrize(
    "value",
    [
        "9999-12-31T23:59:59+01:00",
        "9999-12-31T23:59:59.000+0100",
        "9999-12-31T23:59:59",
        "9999-12-31",
        "0001-01-01T00:00:00+01:00",
        "0001-01-01T00:00:00-01:00",
        "0001-01-01T00:00:00Z",
        "0001-01-01",
        "2101-01-01T00:00:00+01:00",
        "1999-12-31T23:59:59+01:00",
        datetime(9999, 12, 31, 23, 59, 59, tzinfo=CET),
        datetime(9999, 12, 31, 23, 59, 59),
        datetime(1, 1, 1, tzinfo=CET),
        datetime(1, 1, 1, tzinfo=WEST),
        datetime(1, 1, 1),
        datetime(2101, 1, 1, tzinfo=CET),
        datetime(1999, 12, 31, 23, 59, 59, tzinfo=CET),
        datetime.min,
        datetime.max,
        # Midnight written as 24:00 is the next day: beyond the calendar after
        # 31.12.9999 (``OverflowError`` inside the parser), 2101 after 31.12.2100.
        "9999-12-31T24:00:00",
        "9999-12-31T24:00:00+01:00",
        "2100-12-31T24:00:00+01:00",
        # Unreadable, as before.
        "10000-01-01T00:00:00+01:00",
        "kein Datum",
        "",
        "   ",
        None,
        1794298500,
        ["2026-11-10T08:15:00+01:00"],
    ],
)
def test_item_date_at_the_edge_or_unreadable_is_not_set(value: object) -> None:
    assert parse_item_date(value) is None


def test_item_date_as_datetime_is_kept() -> None:
    aware = datetime(2026, 11, 10, 8, 15, tzinfo=CET)
    naive = datetime(2026, 11, 10, 8, 15)
    assert parse_item_date(aware) is aware
    # A datetime without zone is Vienna time, as before.
    _exactly(parse_item_date(naive), datetime(2026, 11, 10, 8, 15, tzinfo=VIENNA))
    first = datetime(2000, 1, 1, tzinfo=CET)
    last = datetime(2100, 12, 31, 23, 59, 59, tzinfo=CET)
    assert parse_item_date(first) is first
    assert parse_item_date(last) is last


# --- ÖBB: _parse_dt_rfc2822 and _parse_period -----------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Tue, 10 Nov 2026 08:15:00 +0100", datetime(2026, 11, 10, 8, 15, tzinfo=CET)),
        ("10 Nov 2026 08:15:00 +0100", datetime(2026, 11, 10, 8, 15, tzinfo=CET)),
        ("Sat, 11 Jul 2026 09:30:00 +0200", datetime(2026, 7, 11, 9, 30, tzinfo=CEST)),
        # A winter time with the summer offset keeps its wall clock.
        ("Fri, 27 Mar 2026 11:42:37 +0200", datetime(2026, 3, 27, 11, 42, 37, tzinfo=CET)),
        ("Tue, 10 Nov 2026 08:15:00 GMT", datetime(2026, 11, 10, 8, 15, tzinfo=UTC)),
        # Without a zone the time is read as UTC.
        ("Tue, 10 Nov 2026 08:15:00", datetime(2026, 11, 10, 8, 15, tzinfo=UTC)),
        ("Tue, 10 Nov 2026 08:15:00 -0000", datetime(2026, 11, 10, 8, 15, tzinfo=UTC)),
        ("Sat, 01 Jan 2000 00:00:00 +0100", datetime(2000, 1, 1, tzinfo=CET)),
        ("Fri, 31 Dec 2100 23:59:59 +0100", datetime(2100, 12, 31, 23, 59, 59, tzinfo=CET)),
    ],
)
def test_oebb_pub_date_is_read_as_before(text: str, expected: datetime) -> None:
    _exactly(_parse_dt_rfc2822(text), expected)


@pytest.mark.parametrize(
    "text",
    [
        "Fri, 31 Dec 9999 23:59:59 +0100",
        "Fri, 31 Dec 9999 23:59:59 -0100",
        "31 Dec 9999 23:59:59 GMT",
        # Without a zone: the path that attaches UTC.
        "31 Dec 9999 23:59:59",
        "31 Dec 9999 23:59:59 -0000",
        "Sat, 01 Jan 2101 00:00:00 +0100",
        "01 Jan 2101 00:00:00",
        "Fri, 31 Dec 1999 23:59:59 +0100",
        "31 Dec 1999 23:59:59",
        "01 Jan 1900 00:00:00 +0100",
        "kein Datum",
        "",
    ],
)
def test_oebb_pub_date_at_the_edge_or_unreadable_is_not_set(text: str) -> None:
    assert _parse_dt_rfc2822(text) is None


def _vienna_day(year: int, month: int, day: int, *, end: bool = False) -> datetime:
    return datetime(year, month, day, 23, 59, 59, tzinfo=VIENNA) if end else datetime(year, month, day, tzinfo=VIENNA)


@pytest.mark.parametrize(
    ("description", "start", "end"),
    [
        ("03.10.2026 - 05.10.2026<br/><br/>Wegen Bauarbeiten", _vienna_day(2026, 10, 3), _vienna_day(2026, 10, 5, end=True)),
        ("03.10.2026-05.10.2026<br>Wegen Bauarbeiten", _vienna_day(2026, 10, 3), _vienna_day(2026, 10, 5, end=True)),
        ("15.12.2026 - 10.01.2027<br/>Wegen Bauarbeiten", _vienna_day(2026, 12, 15), _vienna_day(2027, 1, 10, end=True)),
        # A single day starts and ends on that day.
        ("01.11.2026<br/><br/>Wegen Bauarbeiten", _vienna_day(2026, 11, 1), _vienna_day(2026, 11, 1, end=True)),
        ("01.11.2026 • Wegen Bauarbeiten", _vienna_day(2026, 11, 1), _vienna_day(2026, 11, 1, end=True)),
        ("01.11.2026", _vienna_day(2026, 11, 1), _vienna_day(2026, 11, 1, end=True)),
        # Both boundaries are inclusive.
        ("01.01.2000 - 31.12.2100<br/>Text", _vienna_day(2000, 1, 1), _vienna_day(2100, 12, 31, end=True)),
        ("01.01.2000<br/>Text", _vienna_day(2000, 1, 1), _vienna_day(2000, 1, 1, end=True)),
        ("31.12.2100<br/>Text", _vienna_day(2100, 12, 31), _vienna_day(2100, 12, 31, end=True)),
    ],
)
def test_oebb_period_is_read_as_before(description: str, start: datetime, end: datetime) -> None:
    got_start, got_end = _parse_period(description)
    _exactly(got_start, start)
    _exactly(got_end, end)
    assert isinstance(got_start, datetime) and got_start.tzinfo is not None
    assert str(got_start.tzinfo) == "Europe/Vienna"


@pytest.mark.parametrize(
    ("description", "start", "end"),
    [
        # Each side on its own: the side with the edge year is not set, the other stays.
        ("05.12.2026 - 31.12.9999<br/>Text", _vienna_day(2026, 12, 5), None),
        ("10.11.2026 - 01.01.2101<br/>Text", _vienna_day(2026, 11, 10), None),
        ("31.12.2100 - 01.01.2101<br/>Text", _vienna_day(2100, 12, 31), None),
        ("01.01.0001 - 07.12.2026<br/>Text", None, _vienna_day(2026, 12, 7, end=True)),
        ("31.12.1999 - 10.11.2026<br/>Text", None, _vienna_day(2026, 11, 10, end=True)),
        ("31.12.1999 - 01.01.2000<br/>Text", None, _vienna_day(2000, 1, 1, end=True)),
    ],
)
def test_oebb_period_with_one_edge_side_keeps_the_other_side(
    description: str, start: datetime | None, end: datetime | None
) -> None:
    got_start, got_end = _parse_period(description)
    for got, expected in ((got_start, start), (got_end, end)):
        if expected is None:
            assert got is None
        else:
            _exactly(got, expected)


@pytest.mark.parametrize(
    "description",
    [
        # A single day at the edge is both sides; so are two edge days.
        "31.12.9999<br/>Text",
        "31.12.9999 - 31.12.9999<br/>Text",
        "01.01.0001<br/>Text",
        "01.01.0001 - 31.12.9999<br/>Text",
        "01.01.2101<br/>Text",
        "01.01.2101 - 02.01.2101<br/>Text",
        "31.12.1999<br/>Text",
        "30.12.1999 - 31.12.1999<br/>Text",
        # The paths that gave no period before: no prefix, no such day, end before start.
        "Wegen Bauarbeiten können Züge ausfallen.",
        "Text<br/>03.10.2026 - 05.10.2026",
        "",
        "31.02.2026<br/>Text",
        "03.10.2026 - 31.02.2027<br/>Text",
        "01.01.0000<br/>Text",
        "05.10.2026 - 03.10.2026<br/>Text",
        "31.12.9999 - 01.01.0001<br/>Text",
        # End before start stays no period, also when only one side is plausible.
        "31.12.9999 - 10.11.2026<br/>Text",
        "10.11.2026 - 01.01.0001<br/>Text",
    ],
)
def test_oebb_period_at_the_edge_or_unreadable_is_no_period(description: str) -> None:
    assert _parse_period(description) == (None, None)


# --- Stadt Wien: baustellen_events._parse_datetime ------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # The date with ``Z``: Vienna midnight of that day.
        ("2026-03-22Z", datetime(2026, 3, 22, tzinfo=VIENNA)),
        ("2026-07-11Z", datetime(2026, 7, 11, tzinfo=VIENNA)),
        (" 2026-03-22Z ", datetime(2026, 3, 22, tzinfo=VIENNA)),
        ("2000-01-01Z", datetime(2000, 1, 1, tzinfo=VIENNA)),
        ("2100-12-31Z", datetime(2100, 12, 31, tzinfo=VIENNA)),
        # A time with an offset is moved to Vienna time.
        ("2026-03-22T08:00:00+01:00", datetime(2026, 3, 22, 8, 0, tzinfo=VIENNA)),
        ("2026-03-22T08:00:00Z", datetime(2026, 3, 22, 9, 0, tzinfo=VIENNA)),
        ("2026-07-01T08:00:00+00:00", datetime(2026, 7, 1, 10, 0, tzinfo=VIENNA)),
        # A time without an offset is Vienna time.
        ("2026-03-22T08:00:00", datetime(2026, 3, 22, 8, 0, tzinfo=VIENNA)),
        ("2026-03-22", datetime(2026, 3, 22, tzinfo=VIENNA)),
        ("22.03.2026", datetime(2026, 3, 22, tzinfo=VIENNA)),
        # A number is seconds since 1970.
        (1794298500, datetime(2026, 11, 10, 9, 15, tzinfo=VIENNA)),
        (1794298500.5, datetime(2026, 11, 10, 9, 15, 0, 500000, tzinfo=VIENNA)),
        (946681200, datetime(2000, 1, 1, tzinfo=VIENNA)),
        (4133977199, datetime(2100, 12, 31, 23, 59, 59, tzinfo=VIENNA)),
    ],
)
def test_wfs_date_is_read_as_before(value: str | float, expected: datetime) -> None:
    result = parse_wfs_date(value)
    _exactly(result, expected)
    assert result is not None and str(result.tzinfo) == "Europe/Vienna"


@pytest.mark.parametrize(
    "value",
    [
        # The date with ``Z``.
        "9999-12-31Z",
        "0001-01-01Z",
        "2101-01-01Z",
        "1999-12-31Z",
        "2026-02-31Z",
        # A time with or without an offset.
        "9999-12-31T23:59:59+01:00",
        "9999-12-31T23:59:59Z",
        "9999-12-31T23:59:59",
        "9999-12-31",
        "0001-01-01T00:00:00+01:00",
        "0001-01-01T00:00:00-01:00",
        "0001-01-01T00:00:00",
        "0001-01-01",
        "2101-01-01T00:00:00+01:00",
        "1999-12-31T23:59:59+01:00",
        "31.12.9999",
        # A number: the last second of 9999, the first of year 1, both
        # sides of the boundaries, 1970, and one beyond the calendar.
        253402297199,
        -62135596800,
        4133977200,
        946681199,
        0,
        1e20,
        -1e20,
        # Nothing to read, as before.
        None,
        "",
        "   ",
        "kein Datum",
    ],
)
def test_wfs_date_at_the_edge_or_unreadable_is_not_set(value: str | float | None) -> None:
    assert parse_wfs_date(value) is None
