"""The section "Das Jahr im Überblick" of a year page (``year_stats``).

Michael's decision of 2026-11-10, point 3: the page of a finished year
summarises all cancellations and delays in a statistic. The ledgers stay and
the page is rebuilt from them (Michael, 2026-11-10 19:13 Europe/Vienna);
these tests pin every cell against values worked out by hand.

This file holds the synthetic year: every part of the section against the
year of ``tests/_year_stats_support.py``. The other subjects have their own
files: ``test_year_stats_ranking.py`` (ties and cuts),
``test_year_stats_partial_years.py`` (empty inputs, recorded months),
``test_year_stats_calendar.py`` (the Vienna calendar),
``test_year_stats_formats.py`` (threshold, formats, rounding, escaping),
``test_year_stats_coverage_note.py`` and ``test_year_stats_real_ledger.py``.
"""

from __future__ import annotations

import random

from src.markdown_stats.rows import NO_LINE_LABEL
from src.markdown_stats.year_stats import (
    MIN_DAY_OBSERVATIONS,
    NOT_RECORDED,
    render_year_overview,
)
from tests._year_stats_support import (
    AUSFALL_DAYS,
    DELAY_DAYS,
    DIRECTIONS,
    MONTHS,
    PERIOD,
    _day_of_delays,
    _header,
    _page,  # noqa: F401 - the fixture ``page``
    _section,
    _synthetic_year,
    _table,
)


def test_section_opens_with_heading_and_the_meaning_of_an_observation(page: list[str]) -> None:
    assert page[0] == "## Das Jahr im Überblick"
    assert page[1] == ""
    assert page[2].startswith("Eine Beobachtung ist eine Zeile je Richtung und Abfrage: ")
    # The place, and the R, REX and CJX trains the monitor takes as well.
    assert (
        "mittlere Verspätung aller dabei am Wien Hauptbahnhof beobachteten Abfahrten "
        "auf der Stammstrecke (S-Bahn, R, REX und CJX); "
    ) in page[2]
    # The highest delay of a table is the highest row (the mean of one poll),
    # not the delay of one train; the sentence says so. The rounding sentence
    # follows it (test_year_stats_formats.py).
    assert "„Höchste Verspätung“ nennt die höchste Beobachtung. Die Verspätungen " in page[2]
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
        ["Juni", "19", "29.5 min", "10", "50.0 min", "0", "0"],
        ["Juli", "12", "8.3 min", "2", "50.0 min", "0", "0"],
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
    # Cancellations run from February to May; the monitor that writes them
    # also wrote delay rows until July, so June and July are a 0, August not.
    assert rows["Jänner"][5] == "–"
    assert rows["März"][5] == "0"
    assert (rows["Juni"][5], rows["Juli"][5], rows["August"][5]) == ("0", "0", "–")
    # The legend under the table says what the sign means.
    legend = [line for line in _section(page, MONTHS) if line.startswith("„–“")]
    assert len(legend) == 1
    assert legend[0] == (
        "„–“: Der Monat liegt vor der ersten oder nach der letzten Zeile der Datenreihe, "
        "oder die Datenreihe hat keine Zeile (siehe „Erfasster Zeitraum“). Ausfälle gelten "
        "auch in jedem Monat mit Stammstrecke-Beobachtungen als erfasst. Für einen "
        "erfassten Monat ohne Zeile steht 0; Mittel- und Höchstwert gibt es dort nicht."
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
        "(Tage mit Beobachtungen insgesamt: 6), geordnet nach dem ungerundeten "
        "Mittelwert; bei gleichem Mittelwert steht der frühere Tag zuerst."
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
