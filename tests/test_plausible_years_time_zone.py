"""The plausible years where a time stamp is moved to another time zone.

Operator decision of 2026-11-10: a time stamp whose year lies outside
2000 to 2100 counts as not set (``src.utils.vienna_clock.PLAUSIBLE_YEARS``).
``plausible_moment`` reads the year as it is written. Two readers move the
value to another time zone afterwards, and within hours of a boundary the
year then differs from the written one:

* ``baustellen_events._parse_datetime`` returns Vienna time. It checks the
  year the source wrote and the year in Vienna time, so it never returns a
  year outside the range. A number (seconds since 1970) names no year of
  its own; only its Vienna year is checked.
* ``feed_build.base._parse_state_time`` returns UTC. It reads the value
  through ``_state_stamp``, which checks the year as written. A state value
  outside the range is unreadable (``None``): one that can be moved to UTC
  (``9999-12-31T23:59:59+00:00``, returned as it stood before) and one that
  cannot (``0001-01-01T00:00:00+01:00``, on which the function raised
  ``OverflowError`` before). A value written in 2000 or 2100 is read, also
  when its UTC year is 1999 or 2101.

The other parsers and every edge value are pinned in
``tests/test_plausible_years.py``, the state file from the loader to the
feed in ``tests/test_plausible_years_state.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from src.feed_build.base import _parse_first_seen, _parse_state_time
from src.providers.baustellen_events import _feature_to_event
from src.providers.baustellen_events import _parse_datetime as parse_wfs_date

VIENNA = ZoneInfo("Europe/Vienna")


def _vienna(year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=VIENNA)


# --- Stadt Wien: the written year and the Vienna year ---------------------------


@pytest.mark.parametrize(
    "value",
    [
        # Written 2100, in Vienna 00:30 or 01:30 on 01.01.2101: the Vienna year is outside.
        "2100-12-31T23:30:00Z",
        "2100-12-31T23:30:00-01:00",
        # Written 2000, in Vienna 23:30 on 31.12.1999: the Vienna year is outside.
        "2000-01-01T00:30:00+02:00",
        # Written 1999, in Vienna 00:30 on 01.01.2000: the written year is outside.
        "1999-12-31T23:30:00Z",
        # Written 2101, in Vienna 23:30 on 31.12.2100: the written year is outside.
        "2101-01-01T00:30:00+02:00",
        # A number has no written year. 23:30 UTC on 31.12.2100 is 2101 in Vienna.
        4133979000,
    ],
)
def test_wfs_date_is_not_set_when_the_written_or_the_vienna_year_is_outside(value: str | int) -> None:
    assert parse_wfs_date(value) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # Written and Vienna year agree, an hour inside each boundary.
        ("2100-12-31T22:30:00Z", _vienna(2100, 12, 31, 23, 30)),
        ("2000-01-01T01:30:00+02:00", _vienna(2000, 1, 1, 0, 30)),
        ("2000-01-01T00:00:00+01:00", _vienna(2000, 1, 1)),
        ("2100-12-31T23:59:59+01:00", _vienna(2100, 12, 31, 23, 59, 59)),
        # A number is judged by its Vienna year alone: 23:30 UTC on 31.12.1999 is 2000 there.
        (946683000, _vienna(2000, 1, 1, 0, 30)),
    ],
)
def test_wfs_date_next_to_a_boundary_is_read_when_both_years_are_inside(value: str | int, expected: datetime) -> None:
    result = parse_wfs_date(value)

    assert result is not None
    assert result == expected
    assert result.utcoffset() == expected.utcoffset()
    assert str(result.tzinfo) == "Europe/Vienna"


@pytest.mark.parametrize(
    ("end", "ends_at"),
    [
        ("2100-12-31T22:30:00Z", _vienna(2100, 12, 31, 23, 30)),
        ("2100-12-31T23:30:00Z", None),
        ("1999-12-31T23:30:00Z", None),
    ],
)
def test_construction_site_never_ends_in_a_year_outside_the_range(end: str, ends_at: datetime | None) -> None:
    feature = {
        "properties": {"BEZEICHNUNG": "Baustelle Teststraße", "OBJEKT_BEGINN": "2026-11-01Z", "OBJEKT_ENDE": end},
        "geometry": {},
    }

    event = _feature_to_event(feature)

    assert event is not None
    assert event.ends_at == ends_at


# --- Feed chain: a time kept in the state ---------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        # Neither can be moved to UTC.
        "0001-01-01T00:00:00+01:00",
        "9999-12-31T23:59:59-01:00",
        # Unreadable, as before.
        "kein Datum",
        "",
        5,
    ],
)
def test_state_time_that_cannot_be_read_or_moved_to_utc_is_not_set(value: object) -> None:
    fallback = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)

    assert _parse_state_time({"first_seen": value}, "first_seen") is None
    assert _parse_first_seen({"first_seen": value}, fallback) is fallback


@pytest.mark.parametrize(
    "value",
    [
        # Each can be moved to UTC, with or without an offset.
        "9999-12-31T23:59:59+00:00",
        "9999-12-31T23:59:59+01:00",
        "9999-12-31T23:59:59",
        "0001-01-01T00:00:00+00:00",
        "0001-01-01T00:00:00-01:00",
        "0001-01-01T00:00:00",
        "2101-01-01T00:00:00+00:00",
        "2101-01-01T00:00:00",
        "1999-12-31T23:59:59+00:00",
        "1999-12-31T23:59:59",
        # Written 1999 and 2101; in UTC 00:30 on 01.01.2000 and 23:30 on 31.12.2100.
        "1999-12-31T23:30:00-01:00",
        "2101-01-01T00:30:00+01:00",
    ],
)
@pytest.mark.parametrize("field", ["first_seen", "last_seen", "earliest_published"])
def test_state_time_outside_the_plausible_years_is_not_set(field: str, value: str) -> None:
    fallback = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)

    assert _parse_state_time({field: value}, field) is None
    assert _parse_first_seen({"first_seen": value}, fallback) is fallback


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-11-10T08:15:00+01:00", datetime(2026, 11, 10, 7, 15, tzinfo=UTC)),
        ("2026-11-10T07:15:00+00:00", datetime(2026, 11, 10, 7, 15, tzinfo=UTC)),
        # A value without offset is UTC.
        ("2026-11-10T07:15:00", datetime(2026, 11, 10, 7, 15, tzinfo=UTC)),
        # Both boundaries are inclusive.
        ("2000-01-01T00:00:00+00:00", datetime(2000, 1, 1, tzinfo=UTC)),
        ("2000-01-01T00:00:00", datetime(2000, 1, 1, tzinfo=UTC)),
        ("2100-12-31T23:59:59+00:00", datetime(2100, 12, 31, 23, 59, 59, tzinfo=UTC)),
        ("2100-12-31T23:59:59.999999", datetime(2100, 12, 31, 23, 59, 59, 999999, tzinfo=UTC)),
        # Written 2000 and 2100: read, and returned in UTC as 1999 and 2101.
        ("2000-01-01T00:30:00+01:00", datetime(1999, 12, 31, 23, 30, tzinfo=UTC)),
        ("2100-12-31T23:30:00-01:00", datetime(2101, 1, 1, 0, 30, tzinfo=UTC)),
    ],
)
def test_ordinary_state_time_is_read_as_before(value: str, expected: datetime) -> None:
    result = _parse_state_time({"first_seen": value}, "first_seen")

    assert result == expected
    assert result is not None and result.utcoffset() == expected.utcoffset()
    assert _parse_state_time({"first_seen": value}, "last_seen") is None
    assert _parse_state_time(None, "first_seen") is None
