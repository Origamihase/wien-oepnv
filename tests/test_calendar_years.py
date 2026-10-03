"""Clock changes, turns of the year and leap days from 2026 to 2040.

``test_clock_change.py`` pins the night of 25.10.2026 with real data. This
file runs the same rules through every year up to 2040: the last Sunday in
October and March (including the years in which it falls on the 25th, the
earliest possible day, or the 31st, the latest), every New Year's Eve, and
the leap days 2028, 2032, 2036 and 2040. Before each change the real feed
runs of a September night were replayed onto all these dates (report
``zeitumstellung-jahre-2026-10-03.md`` in the project files); the tests
keep the rules that replay checked.
"""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import src.build_feed as bf  # noqa: E402
from scripts import generate_markdown_stats as stats_script  # noqa: E402
from scripts import update_stammstrecke_hbf as hbf  # noqa: E402
from src.feed import stammstrecke as stammstrecke_module  # noqa: E402
from src.providers.oebb import _parse_dt_rfc2822  # noqa: E402
from src.providers.wl_text import (  # noqa: E402
    _resolve_missing_year,
    extract_end_from_description,
    extract_start_from_description,
)
from src.utils.stats import STOERUNGEN_HEADER, StammstreckeObservation  # noqa: E402

VIENNA = ZoneInfo("Europe/Vienna")
NNBSP = "\u202f"
YEARS = range(2026, 2041)
WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


def _last_sunday(year: int, month: int) -> date:
    last = date(year, month, 31)
    return last - timedelta(days=(last.weekday() + 1) % 7)


def _local(when: datetime) -> datetime:
    return when.astimezone(VIENNA)


def _wall(day: date, hour: int, minute: int = 0, *, fold: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=VIENNA, fold=fold)


def _line(start: datetime, end: datetime | None, now: datetime, since: datetime | None = None) -> str:
    return bf.format_local_times(start, end, now, since=since).replace(NNBSP, " ")


def test_the_years_cover_both_extremes_of_the_last_sunday() -> None:
    autumn = {_last_sunday(year, 10).day for year in YEARS}
    spring = {_last_sunday(year, 3).day for year in YEARS}
    assert {25, 31} <= autumn
    assert {25, 31} <= spring


# ---- Clock changes -------------------------------------------------------


@pytest.mark.parametrize("year", YEARS)
def test_summer_time_ends_on_the_last_sunday_in_october(year: int) -> None:
    day = _last_sunday(year, 10)
    first = _wall(day, 2, 26)
    second = _wall(day, 2, 1, fold=1)
    assert first.utcoffset() == timedelta(hours=2)
    assert second.utcoffset() == timedelta(hours=1)
    # An incident from 02:26 summer time, 35 minutes before a build at
    # 02:01 winter time.
    assert _line(first, None, second, first) == "Seit 02:26"
    # The day has 25 hours and is one day.
    assert _line(_wall(day, 0, 0), _wall(day, 23, 59), second) == "Heute"


@pytest.mark.parametrize("year", YEARS)
def test_summer_time_begins_on_the_last_sunday_in_march(year: int) -> None:
    day = _last_sunday(year, 3)
    before = _wall(day, 1, 50)
    after = _wall(day, 3, 5)
    assert before.utcoffset() == timedelta(hours=1)
    assert after.utcoffset() == timedelta(hours=2)
    assert (after.astimezone(UTC) - before.astimezone(UTC)) == timedelta(minutes=15)
    assert _line(before, None, after, before) == "Seit 01:50"
    assert _line(_wall(day, 0, 0), _wall(day, 23, 59), after) == "Heute"


@pytest.mark.parametrize("year", YEARS)
def test_vao_delay_counts_real_minutes_in_every_year(year: int) -> None:
    spring = _last_sunday(year, 3).isoformat()
    autumn = _last_sunday(year, 10).isoformat()
    # Due 01:55, gone 03:05: ten real minutes in spring, 130 in autumn.
    assert hbf._departure_delay_minutes({"date": spring, "time": "01:55:00", "rtTime": "03:05:00"}) == 10.0
    assert hbf._departure_delay_minutes({"date": autumn, "time": "01:55:00", "rtTime": "03:05:00"}) == 130.0


def _as_written(when: datetime) -> datetime:
    offset = when.astimezone(VIENNA).utcoffset()
    assert offset is not None
    return when.astimezone(timezone(offset))


@pytest.mark.parametrize("year", YEARS)
@pytest.mark.parametrize("month", [3, 10])
def test_stammstrecke_window_is_one_real_hour_in_every_year(year: int, month: int, tmp_path: Path) -> None:
    # 03:30 on the day of the change; delayed ticks 20 and 50 minutes ago
    # count, ticks 80 and 110 minutes ago do not.
    now = _wall(_last_sunday(year, month), 3, 30).astimezone(UTC)
    for minutes_ago, expected in (([50, 20], 1), ([110, 80], 0)):
        rows = [
            StammstreckeObservation(
                timestamp=_as_written(now - timedelta(minutes=ago)), direction="Praterstern", delay_minutes=12.0
            )
            for ago in minutes_ago
        ]
        with patch.object(stammstrecke_module, "read_recent_stammstrecke_observations", return_value=rows):
            events = stammstrecke_module.compute_stammstrecke_events(
                now=now.astimezone(VIENNA), episode_starts_path=tmp_path / f"ep_{minutes_ago[0]}.json"
            )
        assert len(events) == expected


@pytest.mark.parametrize("year", YEARS)
def test_oebb_winter_stamp_with_the_summer_offset_in_every_year(year: int) -> None:
    # ÖBB sends the wall clock with the offset valid at fetch time.
    day = date(year, 1, 15)
    sent = f"{WEEKDAYS_EN[day.weekday()]}, 15 Jan {year} 00:30:00 +0200"
    parsed = _parse_dt_rfc2822(sent)
    assert parsed is not None
    assert parsed.astimezone(VIENNA).replace(tzinfo=None) == datetime(year, 1, 15, 0, 30)


WEEKDAYS_EN = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


# ---- Turn of the year ----------------------------------------------------


@pytest.mark.parametrize("year", YEARS)
def test_time_line_across_new_year(year: int) -> None:
    eve = date(year, 12, 31)
    new = date(year + 1, 1, 1)
    end = _wall(date(year + 1, 1, 6), 23, 59)
    weekday = WEEKDAYS[end.weekday()]
    # On 31.12. the end lies in the next year and carries the year.
    assert _line(_wall(date(year, 12, 28), 0), end, _wall(eve, 12)) == f"Bis {weekday} 06.01.{year + 1}"
    # From 01.01. it is this year's date and drops the year.
    assert _line(_wall(date(year, 12, 28), 0), end, _wall(new, 0, 30)) == f"Bis {weekday} 06.01."
    # A start in the new year, seen on New Year's Eve.
    start = _wall(date(year + 1, 1, 2), 0)
    assert _line(start, end, _wall(eve, 12)) == (
        f"Ab {WEEKDAYS[start.weekday()]} 02.01.{year + 1} bis {weekday} 06.01.{year + 1}"
    )
    # An incident from 23:11 on New Year's Eve, half an hour into the new year.
    began = _wall(eve, 23, 11)
    assert _line(began, None, _wall(eve, 23, 40), began) == "Seit 23:11"
    assert _line(began, None, _wall(new, 0, 30), began) == f"Seit 31.12.{year}"


@pytest.mark.parametrize("year", YEARS)
def test_weekday_is_the_real_one_across_new_year(year: int) -> None:
    today = date(year, 12, 29)
    for offset in range(0, 10):
        day = today + timedelta(days=offset)
        label = bf._time_line_day(_wall(day, 12), today)
        expected = f"{day:%d.%m.}" + (f"{day.year}" if day.year != today.year else "")
        if offset <= 6:
            expected = f"{WEEKDAYS[day.weekday()]} {expected}"
        assert label.replace(NNBSP, " ") == expected


@pytest.mark.parametrize(
    ("text", "published", "start", "end"),
    [
        # WL writes the year only at the start.
        (
            "Zeitraum: Ab Montag, 28. Dezember 2026 bis Mittwoch, 06. Jänner. Maßnahme: Umleitung.",
            datetime(2026, 12, 20, tzinfo=VIENNA),
            date(2026, 12, 28),
            date(2027, 1, 6),
        ),
        # Re-issued after New Year: the end still follows the start.
        (
            "Zeitraum: Ab Montag, 28. Dezember 2026 bis Mittwoch, 06. Jänner. Maßnahme: Umleitung.",
            datetime(2027, 1, 3, tzinfo=VIENNA),
            date(2026, 12, 28),
            date(2027, 1, 6),
        ),
        # Without any year: around the turn of the year the nearest dates.
        (
            "Zeitraum: Ab 28.12. bis 06.01. Maßnahme: Umleitung.",
            datetime(2026, 12, 27, tzinfo=VIENNA),
            date(2026, 12, 28),
            date(2027, 1, 6),
        ),
        (
            "Zeitraum: Ab 28.12. bis 06.01. Maßnahme: Umleitung.",
            datetime(2027, 1, 2, tzinfo=VIENNA),
            date(2026, 12, 28),
            date(2027, 1, 6),
        ),
        # A long measure: "Ende Dezember" eight months after the start was
        # read as the December before and the end was lost.
        (
            "Zeitraum: Ab Freitag, 01. Mai 2026 bis Ende Dezember. Maßnahme: Umleitung.",
            datetime(2026, 5, 1, tzinfo=VIENNA),
            date(2026, 5, 1),
            date(2026, 12, 31),
        ),
        (
            "Zeitraum: Ab Montag, 01. Februar 2027 bis Ende Jänner. Maßnahme: Umleitung.",
            datetime(2027, 2, 1, tzinfo=VIENNA),
            date(2027, 2, 1),
            date(2028, 1, 31),
        ),
        # Leap day.
        (
            "Zeitraum: Ab Montag, 21. Februar 2028 bis Dienstag, 29. Februar. Maßnahme: Umleitung.",
            datetime(2028, 2, 20, tzinfo=VIENNA),
            date(2028, 2, 21),
            date(2028, 2, 29),
        ),
        (
            "Zeitraum: Ab Montag, 21. Februar 2028 bis Ende Februar. Maßnahme: Umleitung.",
            datetime(2028, 2, 20, tzinfo=VIENNA),
            date(2028, 2, 21),
            date(2028, 2, 29),
        ),
        (
            "Zeitraum: Ab Montag, 22. Februar 2027 bis Ende Februar. Maßnahme: Umleitung.",
            datetime(2027, 2, 20, tzinfo=VIENNA),
            date(2027, 2, 22),
            date(2027, 2, 28),
        ),
    ],
)
def test_wl_period_dates_without_a_year(text: str, published: datetime, start: date, end: date) -> None:
    found_start = extract_start_from_description(text, reference_date=published)
    found_end = extract_end_from_description(text, reference_date=published)
    assert found_start is not None and found_start.date() == start
    assert found_end is not None and found_end == datetime(end.year, end.month, end.day, 23, 59, tzinfo=VIENNA)


@pytest.mark.parametrize("year", YEARS)
def test_missing_year_is_the_nearest_across_new_year(year: int) -> None:
    assert _resolve_missing_year(12, 28, datetime(year + 1, 1, 5, tzinfo=VIENNA)) == year
    assert _resolve_missing_year(1, 6, datetime(year, 12, 28, tzinfo=VIENNA)) == year + 1
    # Late on New Year's Eve in UTC is already New Year's Day in Vienna.
    assert _resolve_missing_year(1, 1, datetime(year, 12, 31, 23, 30, tzinfo=UTC)) == year + 1


# ---- Leap years ------------------------------------------------------------


LEAP_YEARS = [year for year in YEARS if year % 4 == 0]


def test_the_leap_years_up_to_2040() -> None:
    assert LEAP_YEARS == [2028, 2032, 2036, 2040]


@pytest.mark.parametrize("year", LEAP_YEARS)
def test_time_line_around_the_leap_day(year: int) -> None:
    leap = date(year, 2, 29)
    tag = WEEKDAYS[leap.weekday()]
    after = WEEKDAYS[(leap + timedelta(days=1)).weekday()]
    assert _line(_wall(leap, 0), None, _wall(date(year, 2, 28), 12)) == f"Ab {tag} 29.02."
    assert _line(_wall(leap, 0), _wall(date(year, 3, 1), 23, 59), _wall(date(year, 2, 28), 12)) == (
        f"Ab {tag} 29.02. bis {after} 01.03."
    )
    assert _line(_wall(date(year, 2, 20), 0), _wall(leap, 23, 59), _wall(leap, 12)) == "Heute"
    # The weekday shows up to six days ahead: 01.03. gets it from 24.02.
    # on, one day later than in other years.
    assert bf._time_line_day(_wall(date(year, 3, 1), 12), date(year, 2, 24)).startswith(after)
    assert bf._time_line_day(_wall(date(year, 3, 1), 12), date(year, 2, 23)) == "01.03."


@pytest.mark.parametrize("year", LEAP_YEARS)
def test_missing_year_finds_the_leap_day(year: int) -> None:
    assert _resolve_missing_year(2, 29, datetime(year, 2, 20, tzinfo=VIENNA)) == year
    assert _resolve_missing_year(2, 29, datetime(year - 1, 12, 1, tzinfo=VIENNA)) == year
    assert _resolve_missing_year(2, 29, datetime(year, 3, 10, tzinfo=VIENNA)) == year


# ---- Statistics dashboard ----------------------------------------------------


def test_stats_year_follows_now_iso(tmp_path: Path) -> None:
    # The default year used to be read from the machine clock when the
    # arguments were parsed, so a run with --now-iso in another year
    # aggregated the wrong file.
    stats = tmp_path / "stats"
    stats.mkdir()
    (stats / "stoerungen_2031.csv").write_text(
        ",".join(STOERUNGEN_HEADER) + "\n2031-01-01T00:10:00+01:00,Mi,00,WL,Karlsplatz\n",
        encoding="utf-8",
    )
    output = tmp_path / "statistik.md"
    rc = stats_script.main(
        [
            "--stats-dir",
            str(stats),
            "--output",
            str(output),
            "--summary-path",
            str(tmp_path / "stats-summary.json"),
            "--skip-readme",
            "--now-iso",
            "2031-01-01T00:15:00+01:00",
        ]
    )
    assert rc == 0
    text = output.read_text(encoding="utf-8")
    assert "Statistik 2031" in text
    assert "| Erfasste Störungen (2031) | 1 |" in text


def _seed_year(stats: Path, year: int, rows: int) -> None:
    lines = "".join(
        f"{year}-12-31T23:{minute:02d}:00+01:00,Fr,23,WL,Karlsplatz\n" for minute in range(rows)
    )
    (stats / f"stoerungen_{year}.csv").write_text(",".join(STOERUNGEN_HEADER) + "\n" + lines, encoding="utf-8")


def _run_stats(tmp_path: Path, now_iso: str, *extra: str) -> int:
    return stats_script.main(
        [
            "--stats-dir",
            str(tmp_path / "stats"),
            "--output",
            str(tmp_path / "statistik.md"),
            "--summary-path",
            str(tmp_path / "stats-summary.json"),
            "--skip-readme",
            "--now-iso",
            now_iso,
            *extra,
        ]
    )


def test_new_year_keeps_the_whole_previous_year(tmp_path: Path) -> None:
    # The last render of 2026 ran on 31.12. at 00:15 and missed New Year's
    # Eve; the first run of 2027 archives all of 2026.
    (tmp_path / "stats").mkdir()
    _seed_year(tmp_path / "stats", 2026, 3)
    assert _run_stats(tmp_path, "2027-01-01T00:15:00+01:00") == 0
    archive = (tmp_path / "statistik-2026.md").read_text(encoding="utf-8")
    assert "Statistik 2026" in archive
    assert "| Erfasste Störungen (2026) | 3 |" in archive
    assert "Statistik 2027" in (tmp_path / "statistik.md").read_text(encoding="utf-8")
    # Written once, never touched again.
    _seed_year(tmp_path / "stats", 2026, 5)
    assert _run_stats(tmp_path, "2027-01-02T00:15:00+01:00") == 0
    assert (tmp_path / "statistik-2026.md").read_text(encoding="utf-8") == archive


def test_no_archive_without_data_or_on_a_readme_tick(tmp_path: Path) -> None:
    (tmp_path / "stats").mkdir()
    assert _run_stats(tmp_path, "2027-01-01T00:15:00+01:00") == 0
    assert not (tmp_path / "statistik-2026.md").exists()
    _seed_year(tmp_path / "stats", 2026, 1)
    assert _run_stats(tmp_path, "2027-01-01T08:00:00+01:00", "--skip-dashboard") == 0
    assert not (tmp_path / "statistik-2026.md").exists()
    # An explicit other year writes no archive either.
    assert _run_stats(tmp_path, "2027-01-01T00:15:00+01:00", "--year", "2026") == 0
    assert not (tmp_path / "statistik-2025.md").exists()
    assert not (tmp_path / "statistik-2026.md").exists()
