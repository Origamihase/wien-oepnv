"""The time line says what matters for today (operator decision 2026-10-02).

Examples are the German feed of 02.10.2026, 20:00 Vienna time.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from zoneinfo import ZoneInfo

import src.build_feed as bf
from src.feed import config as feed_config

VIENNA = ZoneInfo("Europe/Vienna")
NOW = datetime(2026, 10, 2, 20, 0, tzinfo=VIENNA)
# The words are joined with NARROW NO-BREAK SPACE so a display never splits
# the line; the expectations below are written with plain spaces.
NNBSP = "\u202f"


def _line(start: datetime | None, end: datetime | None, now: datetime = NOW) -> str:
    return bf.format_local_times(start, end, now).replace(NNBSP, " ")


def _at(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=VIENNA)


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        # 42: Feuerwehreinsatz, WL's end one hour after the start: no clock time.
        (_at(2026, 10, 2, 19, 10), _at(2026, 10, 2, 20, 10), "Heute"),
        # N71: Ersatzverkehr 01.10.–02.10.: the past start does not matter.
        (_at(2026, 10, 1), _at(2026, 10, 2, 23, 59), "Heute"),
        (_at(2026, 10, 2), _at(2026, 10, 3, 23, 59), "Bis Sa 03.10."),
        (_at(2026, 9, 30), _at(2026, 11, 15), "Bis 15.11."),
        (_at(2026, 10, 5), _at(2026, 11, 11), "Ab Mo 05.10. bis 11.11."),
        (_at(2026, 10, 4, 11, 30), _at(2026, 10, 4, 13), "Am So 04.10."),
        (_at(2026, 11, 1), _at(2026, 11, 1, 23, 59), "Am 01.11."),
        (_at(2026, 10, 13), _at(2026, 10, 14), "Ab 13.10. bis 14.10."),
        (_at(2026, 10, 5), None, "Ab Mo 05.10."),
        (_at(2026, 9, 30), None, "Seit 30.09."),
        (_at(2026, 10, 2, 8), None, "Seit heute"),
        (_at(2021, 2, 28), None, "Seit 28.02.2021"),
        (None, _at(2027, 1, 5), "Bis 05.01.2027"),
        (None, None, ""),
    ],
)
def test_time_line(start: datetime | None, end: datetime | None, expected: str) -> None:
    assert _line(start, end) == expected


def test_a_long_running_item_keeps_a_near_end() -> None:
    # N8: Thaliastraße U, running since 24.07.2024: the span from the start
    # exceeds 540 days, the distance from today does not.
    assert _line(_at(2024, 7, 24), _at(2026, 11, 16)) == "Bis 16.11."


def test_an_absurdly_far_end_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(feed_config, "ABSOLUTE_MAX_AGE_DAYS", 540)
    assert _line(_at(2021, 2, 28), _at(2029, 12, 31)) == "Seit 28.02.2021"


def test_an_end_before_the_start_is_dropped() -> None:
    assert _line(_at(2027, 10, 4), _at(2026, 10, 4, 13)) == "Ab 04.10.2027"


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        # 66A: Busse halten Salvatorianerplatz, ends 03.10. 01:00 (Betriebsschluss).
        (_at(2026, 10, 2, 4, 40), _at(2026, 10, 3, 1), "Heute"),
        # D: Gleisbauarbeiten Althanstraße, works until 07.11. 03:00.
        (_at(2026, 9, 28), _at(2026, 11, 7, 3), "Bis 06.11."),
        # Night works after a later evening: one night, one day.
        (_at(2026, 10, 5, 22), _at(2026, 10, 6, 4), "Am Mo 05.10."),
        # 05:00 opens the next operating day.
        (_at(2026, 9, 28), _at(2026, 11, 7, 5), "Bis 07.11."),
        # Exactly midnight is a date without a clock time.
        (_at(2026, 10, 13), _at(2026, 10, 14), "Ab 13.10. bis 14.10."),
        # Works that begin after midnight keep their own day.
        (_at(2026, 10, 7, 0, 30), _at(2026, 10, 7, 4), "Am Mi 07.10."),
    ],
)
def test_an_end_in_the_small_hours_closes_the_day_before(
    start: datetime, end: datetime, expected: str
) -> None:
    assert _line(start, end) == expected


def test_just_after_midnight_a_night_end_is_still_today() -> None:
    # 00:30 on 03.10.: the end at 01:00 is today, not "Bis 02.10.".
    late = _at(2026, 10, 3, 0, 30)
    assert _line(_at(2026, 10, 2, 4, 40), _at(2026, 10, 3, 1), late) == "Heute"


def test_today_is_the_vienna_day() -> None:
    # 22:30 UTC on 02.10. is 00:30 on 03.10. in Vienna.
    late = datetime(2026, 10, 2, 22, 30, tzinfo=UTC)
    assert _line(_at(2026, 10, 2), _at(2026, 10, 3, 23, 59), late) == "Heute"


@pytest.mark.parametrize(
    ("german", "english"),
    [
        ("[Heute]", "[Today]"),
        ("[Seit heute]", "[Since today]"),
        ("[Bis Sa 03.10.]", "[Until Sat 03.10.]"),
        ("[Ab Mo 05.10. bis 11.11.]", "[From Mon 05.10. until 11.11.]"),
        ("[Am So 04.10.]", "[On Sun 04.10.]"),
        ("[Seit 28.02.2021]", "[Since 28.02.2021]"),
        ("[Ab 13.10. bis 14.10.]", "[From 13.10. until 14.10.]"),
        ("", ""),
    ],
)
def test_time_line_in_english(german: str, english: str) -> None:
    assert bf._translate_time_line_en(german) == english


def test_the_line_never_breaks() -> None:
    line = bf.format_local_times(_at(2026, 10, 5), _at(2026, 11, 11), NOW)
    assert " " not in line
    assert " " not in bf._translate_time_line_en(f"[{line}]")


def test_every_german_word_has_an_english_one() -> None:
    lines = [
        bf.format_local_times(start, end, NOW)
        for start, end in (
            (_at(2026, 10, 2), _at(2026, 10, 2, 23)),
            (_at(2026, 10, 2), None),
            (_at(2026, 10, 3), _at(2026, 10, 9)),
            (_at(2026, 10, 4), _at(2026, 10, 4, 13)),
            (_at(2026, 10, 5), _at(2026, 10, 6)),
            (_at(2026, 10, 6), _at(2026, 10, 7)),
            (_at(2026, 10, 7), _at(2026, 10, 8)),
            (_at(2026, 10, 8), None),
            (_at(2026, 9, 1), _at(2026, 10, 9)),
        )
    ]
    for line in lines:
        english = bf._translate_time_line_en(f"[{line}]")
        leftover = [word for word in english.strip("[]").split() if word in bf._TIME_WORDS_DE_TO_EN]
        assert not leftover, (line, english)
