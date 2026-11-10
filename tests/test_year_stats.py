"""The section "Das Jahr im Überblick" of a year page (``year_stats``).

Michael's decision of 2026-11-10, point 3: the page of a finished year
summarises all cancellations and delays in a statistic. One week later the
raw ledgers are removed, so the figures on the page are the only ones left;
these tests pin every cell against values worked out by hand.
"""

from __future__ import annotations

import random
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.feed.stammstrecke import DELAY_THRESHOLD_MINUTES
from src.markdown_stats import year_stats
from src.markdown_stats.rows import (
    NO_LINE_LABEL,
    AusfallRow,
    StammstreckeRow,
    StoerungRow,
    collect_year_data,
)
from src.markdown_stats.year_stats import (
    MAX_DIRECTION_ROWS,
    MIN_DAY_OBSERVATIONS,
    MONTH_NAMES,
    NOT_RECORDED,
    TOP_DAYS,
    render_year_overview,
)

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


def test_section_opens_with_heading_and_the_meaning_of_an_observation(page: list[str]) -> None:
    assert page[0] == "## Das Jahr im Überblick"
    assert page[1] == ""
    assert page[2].startswith("Eine Beobachtung ist eine Zeile je Richtung und Abfrage: ")
    assert "mittlere Verspätung aller dabei am Wien Hauptbahnhof beobachteten S-Bahn-Abfahrten" in page[2]
    # The highest delay of a table is the highest row (the mean of one poll),
    # not the delay of one train; the sentence says so.
    assert page[2].endswith("„Höchste Verspätung“ nennt die höchste Beobachtung.")
    assert page[-1] == ""
    assert [line for line in page if line.startswith("#")] == [
        "## Das Jahr im Überblick",
        PERIOD,
        MONTHS,
        DIRECTIONS,
        DELAY_DAYS,
        AUSFALL_DAYS,
    ]


def test_recorded_period_of_each_series(page: list[str]) -> None:
    assert _header(page, PERIOD) == ["Datenreihe", "Erste Zeile", "Letzte Zeile"]
    assert _table(page, PERIOD) == [
        ["Stammstrecke-Beobachtungen", "10.03.2026", "03.07.2026"],
        ["Ausfälle", "14.02.2026", "21.05.2026"],
        ["Störungen", "01.01.2026", "31.12.2026"],
    ]


def test_month_table_every_cell(page: list[str]) -> None:
    assert _header(page, MONTHS) == [
        "Monat",
        "Beobachtungen",
        "⌀ Verspätung",
        "Verspätungen > 9 min",
        "Höchste Verspätung",
        "Ausfälle",
        "Störungen",
    ]
    none = ["–", "–", "–", "–"]
    empty = ["0", "–", "0", "–"]
    assert _table(page, MONTHS) == [
        ["Jänner", *none, "–", "2"],
        ["Februar", *none, "3", "0"],
        ["März", "22", "2.0 min", "1", "11.0 min", "0", "0"],
        ["April", *empty, "0", "0"],
        ["Mai", *empty, "7", "0"],
        ["Juni", "19", "29.5 min", "10", "50.0 min", "–", "0"],
        ["Juli", "12", "8.3 min", "2", "50.0 min", "–", "0"],
        ["August", *none, "–", "0"],
        ["September", *none, "–", "0"],
        ["Oktober", *none, "–", "0"],
        ["November", *none, "–", "0"],
        ["Dezember", *none, "–", "1"],
    ]


def test_not_recorded_and_nothing_happened_stay_apart(page: list[str]) -> None:
    """ "–" before the first and after the last row of a series, 0 in between."""
    assert NOT_RECORDED == "–"
    rows = {row[0]: row for row in _table(page, MONTHS)}
    # Delays run from March to July: February is not recorded, April is empty.
    assert rows["Februar"][1] == "–"
    assert rows["April"][1] == "0"
    assert rows["April"][3] == "0"
    # A month without an observation has no mean and no maximum, recorded or not.
    assert rows["April"][2] == "–"
    assert rows["April"][4] == "–"
    assert rows["August"][1] == "–"
    # Cancellations run from February to May, judged by their own rows only:
    # June has delay rows and is still outside the cancellations' period.
    assert rows["Jänner"][5] == "–"
    assert rows["März"][5] == "0"
    assert rows["Juni"][5] == "–"
    # The legend under the table says what the sign means.
    legend = [line for line in _section(page, MONTHS) if line.startswith("„–“")]
    assert len(legend) == 1
    assert legend[0] == (
        "„–“: Der Monat liegt vor der ersten oder nach der letzten Zeile der Datenreihe, "
        "oder die Datenreihe hat keine Zeile (siehe „Erfasster Zeitraum“). Für einen "
        "Monat dazwischen ohne Zeile steht 0; Mittel- und Höchstwert gibt es dort nicht."
    )
    # The legend does not claim that every 0 is a month without a row:
    # here July has ten rows, none of them above the threshold.
    july = render_year_overview(_day_of_delays(7, 3, [0.0] * 10), [], [], year=2026)
    assert _table(july, MONTHS)[6][:4] == ["Juli", "10", "0.0 min", "0"]


def test_delays_per_direction(page: list[str]) -> None:
    assert _header(page, DIRECTIONS) == [
        "Richtung",
        "Beobachtungen",
        "⌀ Verspätung",
        "Verspätungen > 9 min",
        "Höchste Verspätung",
    ]
    assert _table(page, DIRECTIONS) == [
        # 32 rows, sum 44.0; the one row above 9 min is the maximum.
        ["Meidling", "32", "1.4 min", "1", "11.0 min (Mi 11.03.2026)"],
        # 21 rows, sum 661.0; 50.0 on 06.06. and 02.07.: the earlier day is named.
        ["Praterstern", "21", "31.5 min", "12", "50.0 min (Sa 06.06.2026)"],
    ]


def test_days_with_the_highest_mean_delay(page: list[str]) -> None:
    assert _header(page, DELAY_DAYS) == [
        "Tag",
        "⌀ Verspätung",
        "Beobachtungen",
        "Höchste Verspätung",
    ]
    assert _table(page, DELAY_DAYS) == [
        ["Fr 05.06.2026", "11.1 min", "10", "30.0 min"],
        # Same mean on two days: the earlier one first.
        ["Di 10.03.2026", "2.0 min", "12", "2.0 min"],
        ["Mi 11.03.2026", "2.0 min", "10", "11.0 min"],
        ["Fr 03.07.2026", "0.0 min", "10", "0.0 min"],
    ]
    # 06.06. has the highest mean of the year (50.0) but nine rows.
    assert not any("06.06.2026" in line for line in _section(page, DELAY_DAYS))
    note = _section(page, DELAY_DAYS)[-2]
    assert MIN_DAY_OBSERVATIONS == 10
    assert note == (
        "Die Tabelle zeigt bis zu 5 der 4 Tage mit mindestens 10 Beobachtungen "
        "(Tage mit Beobachtungen insgesamt: 6); bei gleichem Mittelwert steht der "
        "frühere Tag zuerst."
    )


def test_days_with_the_most_cancellations(page: list[str]) -> None:
    assert _header(page, AUSFALL_DAYS) == ["Tag", "Ausfälle", "Häufigste Linie"]
    assert _table(page, AUSFALL_DAYS) == [
        # S2 and S3 twice each: the alphabet decides.
        ["Mi 20.05.2026", "4", "S2 (2)"],
        # Three cancellations on two days: the earlier one first.
        ["Sa 14.02.2026", "3", "S1 (2)"],
        ["Do 21.05.2026", "3", f"{NO_LINE_LABEL} (2)"],
    ]
    note = _section(page, AUSFALL_DAYS)[-2]
    assert note.startswith("Die Tabelle zeigt bis zu 5 der 3 Tage mit Ausfällen; ")


def test_output_is_compact_and_every_part_ends_with_an_empty_line(page: list[str]) -> None:
    assert len(page) <= 62
    assert all("\n" not in line for line in page)
    for index, line in enumerate(page):
        if line.startswith("#") and index:
            assert page[index - 1] == ""
            assert page[index + 1] == ""


def test_same_rows_in_another_order_give_the_same_lines(page: list[str]) -> None:
    sm, st, au = _synthetic_year()
    before = (list(sm), list(st), list(au))
    rng = random.Random(20261110)  # noqa: S311 - shuffles test rows, no secret
    rng.shuffle(sm)
    rng.shuffle(st)
    rng.shuffle(au)
    assert render_year_overview(sm, st, au, year=2026) == page
    assert render_year_overview(tuple(sm), tuple(st), tuple(au), year=2026) == page
    assert sorted(sm, key=lambda r: r.timestamp) == sorted(before[0], key=lambda r: r.timestamp)


# ---- Ties and cuts ----------------------------------------------------------


def test_top_days_are_cut_at_five_and_the_earlier_day_wins_the_last_place() -> None:
    """Seven days with the same mean: the five earliest, and the note says seven."""
    assert TOP_DAYS == 5
    days = [(9, 7), (2, 3), (11, 20), (2, 1), (5, 5), (6, 30), (1, 2)]
    sm = [row for month, day in days for row in _day_of_delays(month, day, [4.0] * 10)]
    au = [_au(_at(month, day, 8, i), "S1") for month, day in days for i in range(2)]
    page = render_year_overview(sm, [], au, year=2026)
    earliest = ["Fr 02.01.2026", "So 01.02.2026", "Di 03.02.2026", "Di 05.05.2026", "Di 30.06.2026"]
    assert [row[0] for row in _table(page, DELAY_DAYS)] == earliest
    assert [row[0] for row in _table(page, AUSFALL_DAYS)] == earliest
    assert "bis zu 5 der 7 Tage mit mindestens 10 Beobachtungen" in _section(page, DELAY_DAYS)[-2]
    assert "bis zu 5 der 7 Tage mit Ausfällen" in _section(page, AUSFALL_DAYS)[-2]


def test_higher_value_beats_earlier_day() -> None:
    sm = [
        *_day_of_delays(1, 5, [1.0] * 10),
        *_day_of_delays(8, 9, [3.0] * 10),
        *_day_of_delays(4, 4, [2.0] * 10),
    ]
    au = [
        _au(_at(1, 5, 7), "S1"),
        *(_au(_at(8, 9, 7, i), "S1") for i in range(3)),
        *(_au(_at(4, 4, 7, i), "S1") for i in range(2)),
    ]
    page = render_year_overview(sm, [], au, year=2026)
    order = ["So 09.08.2026", "Sa 04.04.2026", "Mo 05.01.2026"]
    assert [row[0] for row in _table(page, DELAY_DAYS)] == order
    assert [row[0] for row in _table(page, AUSFALL_DAYS)] == order


def test_days_are_ranked_by_the_exact_mean_not_by_the_shown_one() -> None:
    """Both days show 2.0 min; the later one has the higher mean and leads.

    "Bei gleichem Mittelwert steht der frühere Tag zuerst" speaks of the
    mean itself: the earlier day wins only when the two are equal.
    """
    sm = [
        *_day_of_delays(4, 4, [2.0] * 9 + [2.1]),  # mean 2.01
        *_day_of_delays(8, 9, [2.0] * 9 + [2.4]),  # mean 2.04
        *_day_of_delays(9, 9, [2.0] * 9 + [2.4]),  # mean 2.04, later
    ]
    page = render_year_overview(sm, [], [], year=2026)
    assert [row[:2] for row in _table(page, DELAY_DAYS)] == [
        ["So 09.08.2026", "2.0 min"],
        ["Mi 09.09.2026", "2.0 min"],
        ["Sa 04.04.2026", "2.0 min"],
    ]


def test_most_frequent_line_tie_is_alphabetical_regardless_of_case_and_order() -> None:
    for lines in (["S3", "s2", "REX1", "S3", "s2", "REX1"], ["s2", "REX1", "S3", "REX1", "S3", "s2"]):
        au = [_au(_at(3, 3, 9, i), line) for i, line in enumerate(lines)]
        page = render_year_overview([], [], au, year=2026)
        assert _table(page, AUSFALL_DAYS) == [["Di 03.03.2026", "6", "REX1 (2)"]]
    # "s2" stands before "S3" in the alphabet, although "S" sorts before "s".
    for lines in (["S3", "s2", "S3", "s2"], ["s2", "S3", "s2", "S3"]):
        au = [_au(_at(3, 3, 9, i), line) for i, line in enumerate(lines)]
        page = render_year_overview([], [], au, year=2026)
        assert _table(page, AUSFALL_DAYS) == [["Di 03.03.2026", "4", "s2 (2)"]]


def test_no_day_reaches_the_minimum_of_observations() -> None:
    sm = _day_of_delays(3, 10, [5.0] * (MIN_DAY_OBSERVATIONS - 1))
    page = render_year_overview(sm, [], [], year=2026)
    assert _section(page, DELAY_DAYS) == [
        "",
        "_Kein Tag mit mindestens 10 Beobachtungen (Tage mit Beobachtungen: 1)._",
        "",
    ]
    # The month and direction tables still count the nine rows.
    assert _table(page, MONTHS)[2][:5] == ["März", "9", "5.0 min", "0", "5.0 min"]
    assert _table(page, DIRECTIONS) == [["Meidling", "9", "5.0 min", "0", "5.0 min (Di 10.03.2026)"]]


def test_directions_beyond_the_cap_are_named_in_a_line() -> None:
    names = [f"Richtung {chr(ord('A') + i)}" for i in range(MAX_DIRECTION_ROWS + 2)]
    # Three rows each; the last two names get two, so they are the ones cut.
    sm = [
        _sm(_at(3, 10, 6 + n, i), 1.0, name)
        for i, name in enumerate(names)
        for n in range(3 if i < MAX_DIRECTION_ROWS else 2)
    ]
    # The first direction has one row more and leads the table.
    sm.append(_sm(_at(3, 10, 9), 1.0, names[0]))
    page = render_year_overview(sm, [], [], year=2026)
    assert [row[0] for row in _table(page, DIRECTIONS)] == names[:MAX_DIRECTION_ROWS]
    assert [row[1] for row in _table(page, DIRECTIONS)] == ["4", "3", "3", "3", "3", "3"]
    # Two directions, four rows: the line counts both.
    assert _section(page, DIRECTIONS)[-2] == (
        "Nicht einzeln aufgeführt: 2 weitere Richtungsangaben mit zusammen 4 Beobachtungen."
    )


def test_directions_are_ordered_by_observations_before_the_name() -> None:
    """Most observations first; the name only breaks a tie."""
    sm = [
        _sm(_at(3, 10, 6), 1.0, "Meidling"),
        *(_sm(_at(3, 10, 7, i), 1.0, "Praterstern") for i in range(3)),
        *(_sm(_at(3, 10, 8, i), 1.0, "Floridsdorf") for i in range(3)),
    ]
    page = render_year_overview(sm, [], [], year=2026)
    assert [row[:2] for row in _table(page, DIRECTIONS)] == [
        ["Floridsdorf", "3"],
        ["Praterstern", "3"],
        ["Meidling", "1"],
    ]


# ---- Empty inputs and partial years ----------------------------------------


def test_no_rows_no_section() -> None:
    assert render_year_overview([], [], [], year=2026) == []


def test_year_with_only_cancellations() -> None:
    au = [_au(_at(6, 1, 8), "S1"), _au(_at(6, 1, 9), "S1"), _au(_at(8, 15, 9), "REX3")]
    page = render_year_overview([], [], au, year=2026)
    assert _table(page, PERIOD) == [
        ["Stammstrecke-Beobachtungen", "–", "–"],
        ["Ausfälle", "01.06.2026", "15.08.2026"],
        ["Störungen", "–", "–"],
    ]
    months = _table(page, MONTHS)
    assert len(months) == 12
    assert all(row[1:5] == ["–"] * 4 and row[6] == "–" for row in months)
    assert [row[5] for row in months] == ["–"] * 5 + ["2", "0", "1"] + ["–"] * 4
    assert _section(page, DIRECTIONS) == ["", "_Keine Beobachtungen erfasst._", ""]
    assert _section(page, DELAY_DAYS) == ["", "_Keine Beobachtungen erfasst._", ""]
    assert _table(page, AUSFALL_DAYS) == [
        ["Mo 01.06.2026", "2", "S1 (2)"],
        ["Sa 15.08.2026", "1", "REX3 (1)"],
    ]


def test_year_with_only_disruptions() -> None:
    """One Störung is a row of the year: the section is not left out."""
    page = render_year_overview([], [_st(_at(7, 1))], [], year=2026)
    assert _table(page, PERIOD) == [
        ["Stammstrecke-Beobachtungen", "–", "–"],
        ["Ausfälle", "–", "–"],
        ["Störungen", "01.07.2026", "01.07.2026"],
    ]
    months = _table(page, MONTHS)
    assert [row[6] for row in months] == ["–"] * 6 + ["1"] + ["–"] * 5
    assert all(row[1:6] == ["–"] * 5 for row in months)
    # Two series have no row at all; the legend names that reason too.
    legend = next(line for line in _section(page, MONTHS) if line.startswith("„–“"))
    assert "oder die Datenreihe hat keine Zeile" in legend
    assert _section(page, AUSFALL_DAYS) == ["", "_Keine Ausfälle erfasst._", ""]


def test_year_without_cancellations() -> None:
    page = render_year_overview(_day_of_delays(3, 10, [1.0] * 10), [_st(_at(3, 10))], [], year=2026)
    assert _section(page, AUSFALL_DAYS) == ["", "_Keine Ausfälle erfasst._", ""]
    assert [row[5] for row in _table(page, MONTHS)] == ["–"] * 12
    assert [row[6] for row in _table(page, MONTHS)] == ["–", "–", "1"] + ["–"] * 9


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


# ---- Threshold, formats, escaping ------------------------------------------


def test_threshold_text_and_count_come_from_the_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    sm = _day_of_delays(3, 10, [7.5, 7.6, 9.0, 9.1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    page = render_year_overview(sm, [], [], year=2026)
    assert _header(page, MONTHS)[3] == "Verspätungen > 9 min"
    assert _header(page, DIRECTIONS)[3] == "Verspätungen > 9 min"
    assert "„Verspätungen > 9 min“ zählt die Beobachtungen über diesem Wert, " in page[2]
    assert _table(page, MONTHS)[2][3] == "1"

    monkeypatch.setattr(year_stats, "DELAY_THRESHOLD_MINUTES", 7.5)
    page = render_year_overview(sm, [], [], year=2026)
    assert _header(page, MONTHS)[3] == "Verspätungen > 7.5 min"
    assert _header(page, DIRECTIONS)[3] == "Verspätungen > 7.5 min"
    assert "„Verspätungen > 7.5 min“" in page[2]
    # Strictly above: 7.5 itself does not count, 7.6, 9.0 and 9.1 do.
    assert _table(page, MONTHS)[2][3] == "3"
    assert _table(page, DIRECTIONS)[0][3] == "3"


def test_delay_format_has_one_decimal_and_no_negative_zero() -> None:
    sm = _day_of_delays(3, 10, [-0.04] * 10) + _day_of_delays(3, 11, [-2.26] * 10)
    page = render_year_overview(sm, [], [], year=2026)
    assert _table(page, DELAY_DAYS) == [
        ["Di 10.03.2026", "0.0 min", "10", "0.0 min"],
        ["Mi 11.03.2026", "-2.3 min", "10", "-2.3 min"],
    ]
    assert _table(page, MONTHS)[2][:5] == ["März", "20", "-1.1 min", "0", "0.0 min"]


def test_counts_are_plain_integers() -> None:
    sm = [_sm(_at(3, 10, 0, 0) + timedelta(minutes=i), 1.0) for i in range(1234)]
    page = render_year_overview(sm, [], [], year=2026)
    assert _table(page, MONTHS)[2][1] == "1234"
    assert _table(page, DIRECTIONS)[0][1] == "1234"
    assert _table(page, DELAY_DAYS)[0][2] == "1234"


@pytest.mark.parametrize(
    ("raw", "escaped"),
    [
        ("S|1", r"S\|1"),
        ("*x*", r"\*x\*"),
        ("<b>S1</b>", "&lt;b&gt;S1&lt;/b&gt;"),
        ("[S1](https://example.org)", r"\[S1\]\(https://example.org\)"),
        ("S1\n| a | b |", r"S1 \| a \| b \|"),
        ("`S1`", r"\`S1\`"),
    ],
)
def test_ledger_text_cannot_break_a_table(raw: str, escaped: str) -> None:
    sm = _day_of_delays(3, 10, [1.0] * 10, raw)
    au = [_au(_at(3, 10, 7), raw), _au(_at(3, 10, 8), raw)]
    page = render_year_overview(sm, [], au, year=2026)
    assert all("\n" not in line for line in page)
    assert _table(page, DIRECTIONS) == [[escaped, "10", "1.0 min", "0", "1.0 min (Di 10.03.2026)"]]
    assert _table(page, AUSFALL_DAYS) == [["Di 10.03.2026", "2", f"{escaped} (2)"]]
    # The raw text reaches no line of the section.
    assert not any(raw in line for line in page)


def test_long_ledger_text_is_capped() -> None:
    au = [_au(_at(3, 10, 7), "S" * 500)]
    page = render_year_overview([], [], au, year=2026)
    assert _table(page, AUSFALL_DAYS) == [["Di 10.03.2026", "1", f"{'S' * 80} (1)"]]


# ---- The real ledger (read only) -------------------------------------------


def _cell_count(cell: str) -> int:
    return 0 if cell == NOT_RECORDED else int(cell)


def test_real_ledger_renders_and_its_months_add_up() -> None:
    """Smoke test on ``data/stats`` as committed; it reads and writes nothing.

    The 2026 ledgers leave the repository on 8 January 2027 (Michael's
    decision of 2026-11-10, point 5); from then on there is nothing to read.
    """
    year = 2026
    stats_dir = ROOT / "data" / "stats"
    names = [f"{kind}_{year}.csv" for kind in ("stammstrecke", "stoerungen", "ausfaelle")]
    if not all((stats_dir / name).is_file() for name in names):
        pytest.skip(f"ledgers of {year} not present")
    sm, st, au = collect_year_data(year, stats_dir=stats_dir)
    if not (sm or st or au):  # pragma: no cover - header-only ledgers
        pytest.skip(f"ledgers of {year} hold no rows")

    page = render_year_overview(sm, st, au, year=year)

    assert page[0] == "## Das Jahr im Überblick"
    assert len(page) <= 70
    months = _table(page, MONTHS)
    assert [row[0] for row in months] == list(MONTH_NAMES)
    assert all(len(row) == 7 for row in months)
    assert sum(_cell_count(row[1]) for row in months) == len(sm)
    assert sum(_cell_count(row[5]) for row in months) == len(au)
    assert sum(_cell_count(row[6]) for row in months) == len(st)
    exceedances = len([row for row in sm if row.delay_minutes > DELAY_THRESHOLD_MINUTES])
    assert sum(_cell_count(row[3]) for row in months) == exceedances
    directions = _table(page, DIRECTIONS)
    assert all(len(row) == 5 for row in directions)
    if len({row.direction for row in sm}) <= MAX_DIRECTION_ROWS:
        assert sum(int(row[1]) for row in directions) == len(sm)
        assert sum(int(row[3]) for row in directions) == exceedances
    assert all(len(row) == 3 for row in _table(page, PERIOD))
    assert len(_table(page, DELAY_DAYS)) <= TOP_DAYS
    assert len(_table(page, AUSFALL_DAYS)) <= TOP_DAYS
