"""The yearly Baustellen ledger: the year a site is entered in.

The file is the one of the Vienna year of the write, and a site gets a row
in every year it runs in. Of the review of 2026-11-10, each test
names what the writer in ``src/utils/stats_baustellen.py`` did before:

* a site that ended last year got a row in the new year's file,
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Any, Self

import pytest

from scripts import update_baustellen_cache
from src.utils import stats_baustellen
from src.utils.stats import VIENNA_TZ
from src.utils.stats_baustellen import append_baustellen_rows, known_guids
from tests._baustellen_ledger_support import (
    _FEATURES,
    ENDS_2027,
    GUID,
    LEDGER_LOGGER,
    _event,
    _ledger,
    _live_answer,
    _real_cache,
    _records,
    _warnings,
)


# The first cache write of 2027 in Vienna (the cron's first tick after midnight).
NEW_YEAR = datetime(2027, 1, 1, 0, 1, 15, tzinfo=VIENNA_TZ)


@pytest.fixture
def stats_dir(tmp_path: Path) -> Path:
    return tmp_path / "ledger"


@pytest.mark.parametrize(
    ("now", "year"),
    [
        (datetime(2026, 12, 31, 23, 59, 59, tzinfo=VIENNA_TZ), 2026),
        (datetime(2027, 1, 1, 0, 0, 0, tzinfo=VIENNA_TZ), 2027),
        # The same two instants given in UTC: the Vienna year decides.
        (datetime(2026, 12, 31, 22, 59, 59, tzinfo=UTC), 2026),
        (datetime(2026, 12, 31, 23, 0, 0, tzinfo=UTC), 2027),
        # A naive moment is Vienna time, as in ``to_vienna``.
        (datetime(2026, 12, 31, 23, 59, 59), 2026),
        (datetime(2027, 1, 1, 0, 0, 0), 2027),
    ],
    ids=["vienna-2359", "vienna-0000", "utc-2259", "utc-2300", "naive-2359", "naive-0000"],
)
def test_year_file_is_the_vienna_year_of_now(now: datetime, year: int, stats_dir: Path) -> None:
    # Under way in both years: a site that ended before the year of ``now`` gets no row.
    assert append_baustellen_rows([_event(ends_at=ENDS_2027)], now=now, stats_dir=stats_dir) == 1
    assert [p.name for p in stats_dir.iterdir()] == [f"baustellen_{year}.csv"]
    stamp = _records(_ledger(stats_dir, year))[0]["timestamp"]
    assert stamp.startswith(f"{year}-") and stamp.endswith("+01:00")


# --- a site and the year it is entered in ------------------------------------


def test_a_site_still_running_gets_a_row_in_the_new_year_too(stats_dir: Path) -> None:
    """Moved here from ``tests/test_baustellen_ledger.py``; the site now ends in 2027."""
    old_year = datetime(2026, 12, 31, 23, 59, tzinfo=VIENNA_TZ)
    new_year = datetime(2027, 1, 1, 0, 0, tzinfo=VIENNA_TZ)
    event = _event(ends_at=ENDS_2027)
    assert append_baustellen_rows([event], now=old_year, stats_dir=stats_dir) == 1
    assert append_baustellen_rows([event], now=new_year, stats_dir=stats_dir) == 1
    assert append_baustellen_rows([event], now=new_year, stats_dir=stats_dir) == 0
    assert known_guids(2026, stats_dir=stats_dir) == {GUID} == known_guids(2027, stats_dir=stats_dir)
    assert _records(_ledger(stats_dir, 2026))[0]["timestamp"] == "2026-12-31T23:59:00+01:00"
    assert _records(_ledger(stats_dir, 2027))[0]["timestamp"] == "2027-01-01T00:00:00+01:00"


@pytest.mark.parametrize(
    ("ends_at", "written"),
    [
        # The city can still deliver a site on the day after its last day.
        ("2026-12-31T00:00:00+01:00", 0),
        ("2026-12-31T23:59:59+01:00", 0),
        ("2026-09-04T00:00:00+02:00", 0),
        (datetime(2026, 12, 31, 22, 59, 59, tzinfo=UTC), 0),
        # The Vienna date counts: 23:30 UTC on 31 December is 1 January in Vienna.
        ("2026-12-31T23:30:00+00:00", 1),
        ("2027-01-01T00:00:00+01:00", 1),
        (ENDS_2027, 1),
        # An open end, and what the reader of the year page shows as one.
        (None, 1), ("", 1), ("demnächst", 1), ("1999-12-31T00:00:00+01:00", 1),
    ],
)
def test_a_site_that_ended_before_the_year_gets_no_row(ends_at: Any, written: int, stats_dir: Path) -> None:
    event = _event(ends_at=ends_at)
    assert append_baustellen_rows([event, _event(guid="läuft", ends_at=ENDS_2027)], now=NEW_YEAR, stats_dir=stats_dir) == written + 1
    assert known_guids(2027, stats_dir=stats_dir) == ({GUID, "läuft"} if written else {"läuft"})
    # In the year it ran in, the same site is entered, on its last day too.
    last_day = datetime(2026, 12, 31, 23, 59, 59, tzinfo=VIENNA_TZ)
    assert append_baustellen_rows([event], now=last_day, stats_dir=stats_dir) == 1
    assert known_guids(2026, stats_dir=stats_dir) == {GUID}


def test_only_ended_sites_leave_no_file_and_no_log_line(stats_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    assert append_baustellen_rows([_event(), _event(guid="b")], now=NEW_YEAR, stats_dir=stats_dir) == 0
    assert not stats_dir.exists()
    assert [r for r in caplog.records if r.name == LEDGER_LOGGER] == []


def test_hook_at_new_year_keeps_the_cache_and_enters_no_ended_site(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """``_site`` ends on 2026-11-15: after New Year the cache still holds what the city sent, the ledger of 2027 nothing."""
    target = _real_cache(monkeypatch, tmp_path)
    _live_answer(monkeypatch, _FEATURES)
    monkeypatch.setattr(
        update_baustellen_cache, "append_baustellen_rows", lambda events: append_baustellen_rows(events, now=NEW_YEAR)
    )
    caplog.set_level(logging.WARNING)

    assert update_baustellen_cache.main() == 0

    assert len(json.loads(target.read_text(encoding="utf-8"))) == 2
    assert not (tmp_path / "stats").exists()
    assert _warnings(caplog) == []


class _ClockAtNewYear(datetime):
    """A clock that stands at 23:30 UTC on 31 December 2026: 00:30 on 1 January 2027 in Vienna."""

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> Self:
        return cls.fromtimestamp(datetime(2026, 12, 31, 23, 30, tzinfo=UTC).timestamp(), tz)


def test_without_now_the_clock_gives_the_vienna_year_and_the_time_stamp(stats_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The hook passes no ``now``; the hook tests pin it, so the default is checked here against a clock that stands still."""
    monkeypatch.setattr(stats_baustellen, "datetime", _ClockAtNewYear)
    assert append_baustellen_rows([_event(), _event(guid="läuft", ends_at=ENDS_2027)], stats_dir=stats_dir) == 1
    assert [p.name for p in stats_dir.iterdir()] == ["baustellen_2027.csv"]
    [record] = _records(_ledger(stats_dir, 2027))
    assert (record["timestamp"], record["weekday"], record["hour"], record["guid"]) == ("2027-01-01T00:30:00+01:00", "Fr", "00", "läuft")
