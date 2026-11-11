"""More of the section "Das Jahr im Überblick": what the review of 2026-11-10 found.

Each test pins one statement of the page that was false or missing in a
case the first tests (``test_year_stats.py``, at the line limit of
``test_file_line_limit.py``) did not reach: where the coverage note stands,
how a delay is rounded, which months count as recorded, and the notes
under the tables for a count of one.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.markdown_stats import year_stats
from src.markdown_stats.direction_coverage import render_finished_year_coverage_note
from src.markdown_stats.rows import collect_year_data
from src.markdown_stats.year_archive import render_year_archive
from src.markdown_stats.year_stats import (
    MAX_DIRECTION_ROWS,
    MIN_DAY_OBSERVATIONS,
    render_year_overview,
)
from src.utils.stats import to_vienna
from tests.test_year_stats import (
    AUSFALL_DAYS,
    DELAY_DAYS,
    DIRECTIONS,
    MONTHS,
    PERIOD,
    _at,
    _au,
    _day_of_delays,
    _section,
    _sm,
    _st,
    _table,
)

ROOT = Path(__file__).resolve().parents[1]

NOTE = (
    "> ⚠️ **Eingeschränkte Abdeckung:** Von Wien Hbf Richtung **Praterstern** sind auf "
    "der Stammstrecke Fahrten nur bis 14.08.2026 erfasst. Die Jahreszahlen unten "
    "enthalten Richtung **Praterstern** daher nur bis dahin.\n\n"
)


def _half_up(value: Fraction) -> str:
    """*value* with one decimal, a half away from zero, worked out in fractions."""
    tenths = math.floor(abs(value) * 10 + Fraction(1, 2))
    sign = "-" if value < 0 and tenths else ""
    return f"{sign}{tenths // 10}.{tenths % 10} min"


def _exact(delay: float) -> Fraction:
    """The decimal number a ledger cell held, from the float it was read into."""
    return Fraction(Decimal(repr(delay)))


# ---- The coverage note at the head of the section ---------------------------


def test_coverage_note_stands_once_between_the_opening_paragraph_and_the_period() -> None:
    """ "Die Jahreszahlen unten" must stand above every figure of the section."""
    sm = _day_of_delays(3, 10, [1.0] * 10)
    plain = render_year_overview(sm, [], [], year=2026)
    page = render_year_overview(sm, [], [], year=2026, coverage_note=NOTE)

    # Heading, empty line, opening paragraph, empty line: then the note.
    assert page[:4] == plain[:4]
    assert page[2].startswith("Eine Beobachtung ist ")
    assert page[4] == NOTE.rstrip("\n")
    assert page[5] == ""
    assert page[6] == PERIOD
    # Nothing else moves: the page is the plain one plus the note and one empty line.
    assert page[6:] == plain[4:]
    assert sum("Eingeschränkte Abdeckung" in line for line in page) == 1
    assert all("\n" not in line for line in page)
    first_table_row = next(i for i, line in enumerate(page) if line.startswith("|"))
    assert page.index(NOTE.rstrip("\n")) < first_table_row


def test_note_of_the_real_renderer_reaches_the_section_unchanged() -> None:
    """Praterstern ends on 14.08., Meidling runs on: the note names the day."""
    sm = [
        *(row for day in range(1, 29) for row in _day_of_delays(8, day, [1.0] * 2)),
        *(row for day in range(1, 15) for row in _day_of_delays(8, day, [2.0] * 2, "Praterstern")),
    ]
    note = render_finished_year_coverage_note(sm, year=2026)
    assert "Richtung **Praterstern**" in note
    assert "nur bis 14.08.2026 erfasst" in note
    assert note.endswith("\n")

    page = render_year_overview(sm, [], [], year=2026, coverage_note=note)
    assert page[4] == note.rstrip("\n")
    assert page[4].endswith("daher nur bis dahin.")
    assert (page[3], page[5], page[6]) == ("", "", PERIOD)
    # The table the note explains: the two directions with unequal periods.
    assert [row[:2] for row in _table(page, DIRECTIONS)] == [["Meidling", "56"], ["Praterstern", "28"]]


@pytest.mark.parametrize("empty", ["", "\n", "\n\n", "  \n"])
def test_no_note_no_line(empty: str) -> None:
    sm = _day_of_delays(3, 10, [1.0] * 10)
    plain = render_year_overview(sm, [], [], year=2026)
    assert render_year_overview(sm, [], [], year=2026, coverage_note=empty) == plain
    assert plain[4] == PERIOD


def test_note_alone_makes_no_section() -> None:
    """Without a row of the year there is no overview; the caller keeps the note."""
    assert render_year_overview([], [], [], year=2026, coverage_note=NOTE) == []


def test_note_of_several_lines_gives_one_entry_per_line() -> None:
    sm = _day_of_delays(3, 10, [1.0] * 10)
    page = render_year_overview(sm, [], [], year=2026, coverage_note="> eins\n> zwei\n\n")
    assert page[3:8] == ["", "> eins", "> zwei", "", PERIOD]


# ---- Rounding ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("delay", "shown"),
    [
        # Exact halves of a ledger value: the float format gave 3.2, 11.2, 0.2, -2.2.
        (3.25, "3.3 min"),
        (11.25, "11.3 min"),
        (0.25, "0.3 min"),
        (-2.25, "-2.3 min"),
        (29.75, "29.8 min"),
        (0.05, "0.1 min"),
        (-0.05, "-0.1 min"),
        # No half: nothing changes.
        (0.04, "0.0 min"),
        (-0.04, "0.0 min"),
        (11.24, "11.2 min"),
        (11.26, "11.3 min"),
        (7.0, "7.0 min"),
    ],
)
def test_a_ledger_value_is_rounded_half_up(delay: float, shown: str) -> None:
    """A single row: its value is the month's mean and its highest delay."""
    page = render_year_overview([_sm(_at(7, 10), delay)], [], [], year=2026)
    assert _table(page, MONTHS)[6][2] == shown
    assert _table(page, MONTHS)[6][4] == shown
    assert _table(page, DIRECTIONS)[0][2] == shown
    assert _table(page, DIRECTIONS)[0][4] == f"{shown} (Fr 10.07.2026)"


@pytest.mark.parametrize(
    ("delays", "shown"),
    [
        # (0.01 + 0.09) / 2 is 0.049999999999999996 as a float, 0.05 in the ledger.
        ([0.01, 0.09], "0.1 min"),
        ([9.12, 16.18], "12.7 min"),
        ([22.95, 7.55, 21.55], "17.4 min"),
        ([0.15] * 10, "0.2 min"),
        ([-0.01, -0.09], "-0.1 min"),
    ],
)
def test_a_mean_on_a_half_is_rounded_half_up_whatever_the_float_says(
    delays: list[float], shown: str
) -> None:
    exact = sum(map(_exact, delays), Fraction(0)) / len(delays)
    assert exact * 100 % 10 == 5, "the case must be an exact half"
    assert _half_up(exact) == shown
    page = render_year_overview(_day_of_delays(7, 10, delays), [], [], year=2026)
    assert _table(page, MONTHS)[6][2] == shown


def test_every_mean_of_two_ledger_values_is_rounded_as_by_hand() -> None:
    """All pairs from 0.00 to 3.99: the float mean never decides the last digit."""
    values = [cents / 100 for cents in range(400)]
    wrong = [
        (first, second)
        for index, first in enumerate(values)
        for second in values[index:]
        if year_stats._fmt_delay(math.fsum((first, second)) / 2)
        != _half_up((_exact(first) + _exact(second)) / 2)
    ]
    assert wrong == []


@pytest.mark.parametrize("delay", [1e30, -1e30, 1e300, 1.7976931348623157e308])
def test_a_huge_hand_added_value_still_gives_a_page(delay: float) -> None:
    """The default decimal context (28 digits) would raise on these."""
    page = render_year_overview([_sm(_at(7, 10), delay)], [], [], year=2026)
    cell = _table(page, MONTHS)[6][4]
    assert cell.endswith(".0 min")
    assert "E" not in cell.upper()
    assert Fraction(Decimal(cell.removesuffix(" min"))) == _exact(delay)


def test_a_value_that_is_not_finite_does_not_raise() -> None:
    """The parser drops such a row; the formatter must not be what fails."""
    assert year_stats._fmt_delay(math.inf) == "inf min"
    assert year_stats._fmt_delay(-math.inf) == "-inf min"
    assert year_stats._fmt_delay(math.nan) == "nan min"


def test_real_ledger_month_cells_equal_the_values_rounded_by_hand() -> None:
    """Mean and maximum of every month of 2026, worked out in fractions.

    Reads ``data/stats`` as committed and writes nothing; skipped in a
    checkout without the ledger.
    """
    stats_dir = ROOT / "data" / "stats"
    if not (stats_dir / "stammstrecke_2026.csv").is_file():
        pytest.skip("ledger of 2026 not present")
    sm, st, au = collect_year_data(2026, stats_dir=stats_dir)
    if not sm:  # pragma: no cover - header-only ledger
        pytest.skip("ledger of 2026 holds no rows")
    delays_of: dict[int, list[Fraction]] = defaultdict(list)
    for row in sm:
        delays_of[to_vienna(row.timestamp).month].append(_exact(row.delay_minutes))

    months = _table(render_year_overview(sm, st, au, year=2026), MONTHS)
    checked = 0
    for month, cells in enumerate(months, start=1):
        delays = delays_of.get(month)
        if not delays:
            continue
        assert cells[2] == _half_up(sum(delays, Fraction(0)) / len(delays)), cells[0]
        assert cells[4] == _half_up(max(delays)), cells[0]
        checked += 1
    assert checked == len(delays_of) >= 1


# ---- The order of the days and the note under it ----------------------------


def test_note_names_the_unrounded_mean_as_the_order() -> None:
    """Two days show 2.4 min and the later one stands first: the note says why."""
    sm = [*_day_of_delays(6, 20, [2.44] * 10), *_day_of_delays(6, 10, [2.36] * 10)]
    page = render_year_overview(sm, [], [], year=2026)
    assert [row[:2] for row in _table(page, DELAY_DAYS)] == [
        ["Sa 20.06.2026", "2.4 min"],
        ["Mi 10.06.2026", "2.4 min"],
    ]
    assert _section(page, DELAY_DAYS)[-2] == (
        "Die Tabelle zeigt bis zu 5 der 2 Tage mit mindestens 10 Beobachtungen "
        "(Tage mit Beobachtungen insgesamt: 2), geordnet nach dem ungerundeten "
        "Mittelwert; bei gleichem Mittelwert steht der frühere Tag zuerst."
    )


# ---- Which months count as recorded -----------------------------------------


def test_months_between_cancellations_and_delay_rows_are_not_recorded() -> None:
    """Cancellations in January and February, delay rows in June and July.

    March to May lie after the last cancellation row and have no delay row:
    by the legend they are "–", as the delay columns of the same months are.
    """
    sm = [_sm(_at(6, 10), 1.0), _sm(_at(7, 10), 1.0)]
    au = [_au(_at(1, 10), "S1"), _au(_at(2, 10), "S1")]
    months = _table(render_year_overview(sm, [], au, year=2026), MONTHS)
    assert [row[5] for row in months] == ["1", "1", "–", "–", "–", "0", "0"] + ["–"] * 5
    assert [row[1] for row in months] == ["–"] * 5 + ["1", "1"] + ["–"] * 5
    assert _table(render_year_overview(sm, [], au, year=2026), PERIOD)[:2] == [
        ["Stammstrecke-Beobachtungen", "10.06.2026", "10.07.2026"],
        ["Ausfälle", "10.01.2026", "10.02.2026"],
    ]


def test_cancellations_before_the_delay_rows_in_the_other_order() -> None:
    """Delay rows first, cancellations months later: the gap is "–" as well."""
    sm = [_sm(_at(2, 10), 1.0)]
    au = [_au(_at(6, 10), "S1"), _au(_at(8, 10), "S1")]
    months = _table(render_year_overview(sm, [], au, year=2026), MONTHS)
    assert [row[5] for row in months] == ["–", "0", "–", "–", "–", "1", "0", "1"] + ["–"] * 4


def test_disruptions_are_judged_by_their_own_rows_only() -> None:
    """Delay rows from March to July, one Störung in May.

    The feed build writes the Störungen ledger, not the monitor: delay rows
    say nothing about whether it was being written. Cancellations do follow
    the delay rows, in the same table.
    """
    sm = [*_day_of_delays(3, 10, [1.0] * 10), *_day_of_delays(7, 3, [1.0] * 10)]
    months = _table(render_year_overview(sm, [_st(_at(5, 20))], [], year=2026), MONTHS)
    assert [row[6] for row in months] == ["–"] * 4 + ["1"] + ["–"] * 7
    assert [row[5] for row in months] == ["–", "–"] + ["0"] * 5 + ["–"] * 5


# ---- Notes for a count of one -----------------------------------------------


def test_a_single_day_with_cancellations_is_named_in_the_singular() -> None:
    page = render_year_overview([], [], [_au(_at(7, 10), "S1")], year=2026)
    assert _section(page, AUSFALL_DAYS)[-2] == (
        "Die Tabelle zeigt den einzigen Tag mit Ausfällen; bei gleicher Zahl steht der "
        "frühere Tag zuerst, bei gleich häufigen Linien die im Alphabet erste. In "
        "Klammern: Ausfälle dieser Linie an diesem Tag."
    )
    assert not any("der 1 Tage" in line for line in page)


def test_a_single_day_with_enough_observations_is_named_in_the_singular() -> None:
    sm = [
        *_day_of_delays(7, 10, [1.0] * MIN_DAY_OBSERVATIONS),
        *_day_of_delays(7, 11, [9.0] * (MIN_DAY_OBSERVATIONS - 1)),
    ]
    page = render_year_overview(sm, [], [], year=2026)
    assert _section(page, DELAY_DAYS)[-2] == (
        "Die Tabelle zeigt den einzigen Tag mit mindestens 10 Beobachtungen "
        "(Tage mit Beobachtungen insgesamt: 2), geordnet nach dem ungerundeten "
        "Mittelwert; bei gleichem Mittelwert steht der frühere Tag zuerst."
    )
    assert not any("der 1 Tage" in line for line in page)


@pytest.mark.parametrize(
    ("beyond", "rows_each", "note"),
    [
        (1, 1, "Nicht einzeln aufgeführt: 1 weitere Richtungsangabe mit 1 Beobachtung."),
        (1, 2, "Nicht einzeln aufgeführt: 1 weitere Richtungsangabe mit 2 Beobachtungen."),
        (2, 1, "Nicht einzeln aufgeführt: 2 weitere Richtungsangaben mit zusammen 2 Beobachtungen."),
    ],
)
def test_directions_beyond_the_cap_in_singular_and_plural(
    beyond: int, rows_each: int, note: str
) -> None:
    """The directions in the table get three rows each, those beyond it fewer."""
    names = [f"Richtung {chr(ord('A') + i)}" for i in range(MAX_DIRECTION_ROWS + beyond)]
    sm = [
        _sm(_at(3, 10, 6 + n, i), 1.0, name)
        for i, name in enumerate(names)
        for n in range(3 if i < MAX_DIRECTION_ROWS else rows_each)
    ]
    page = render_year_overview(sm, [], [], year=2026)
    assert [row[0] for row in _table(page, DIRECTIONS)] == names[:MAX_DIRECTION_ROWS]
    assert _section(page, DIRECTIONS)[-2] == note


# ---- Second round of the review of 2026-11-10 -------------------------------

ROUNDING = (
    "Die Verspätungen dieses Abschnitts sind auf eine Nachkommastelle kaufmännisch "
    "gerundet; „Kennzahlen auf einen Blick“ und die Balkendiagramme weiter unten runden "
    "wie das Dashboard."
)
# Ten ledger values each, both summing to 20.92: the same mean 2.092, but the
# floats are 2.0919999999999996 and 2.092.
LOW_FLOAT = [2.6, 0.55, 3.98, 0.83, 2.66, 2.01, 1.89, 2.5, 3.75, 0.15]
HIGH_FLOAT = [1.52, 1.63, 1.8, 1.39, 1.66, 3.83, 3.83, 2.66, 2.56, 0.04]


@pytest.mark.parametrize(("first", "second"), [(LOW_FLOAT, HIGH_FLOAT), (HIGH_FLOAT, LOW_FLOAT)])
def test_equal_ledger_means_put_the_earlier_day_first_whatever_their_floats(
    first: list[float], second: list[float]
) -> None:
    """The note promises the earlier day first for an equal mean; the float noise must not decide."""
    assert sum(_exact(v) for v in first) == sum(_exact(v) for v in second) == Fraction("20.92")
    assert math.fsum(first) / 10 != math.fsum(second) / 10
    sm = [*_day_of_delays(6, 20, second), *_day_of_delays(6, 10, first)]
    page = render_year_overview(sm, [], [], year=2026)
    assert [row[:2] for row in _table(page, DELAY_DAYS)] == [
        ["Mi 10.06.2026", "2.1 min"],
        ["Sa 20.06.2026", "2.1 min"],
    ]


def test_a_mean_a_hair_higher_still_leads() -> None:
    """The rounding that joins equal means does not join different ones: 2.093 beats 2.092."""
    sm = [*_day_of_delays(6, 10, LOW_FLOAT), *_day_of_delays(6, 20, [*HIGH_FLOAT[:-1], 0.05])]
    page = render_year_overview(sm, [], [], year=2026)
    assert [row[0] for row in _table(page, DELAY_DAYS)] == ["Sa 20.06.2026", "Mi 10.06.2026"]


def test_opening_paragraph_ends_with_the_rounding_of_the_page() -> None:
    page = render_year_overview([_sm(_at(7, 10), 3.25)], [], [], year=2026)
    assert page[2].endswith("„Höchste Verspätung“ nennt die höchste Beobachtung. " + ROUNDING)
    assert year_stats.ROUNDING_NOTE == ROUNDING
    assert sum(ROUNDING in line for line in page) == 1


def test_the_page_rounds_two_ways_and_says_so(tmp_path: Path) -> None:
    """One row of 3.25 min: the overview prints 3.3, the Kennzahlen above and the bars below 3.2.

    Below the overview no table prints a delay (they hold counts); the bar
    charts do, which is why the legend names them.
    """
    stats = tmp_path / "stats"
    stats.mkdir()
    (stats / "stammstrecke_2026.csv").write_text(
        "timestamp,weekday,hour,direction,delay_minutes\n"
        "2026-07-10T08:00:00+02:00,Fr,08,Meidling,3.25\n",
        encoding="utf-8",
    )
    page = render_year_archive(
        now=datetime(2027, 1, 1, 0, 5, tzinfo=ZoneInfo("Europe/Vienna")),
        year=2026,
        stats_dir=stats,
        output_path=tmp_path / "statistik-2026.md",
    )
    assert page is not None
    lines = page.splitlines()
    kennzahlen, overview, stammstrecke = (
        lines.index(heading)
        for heading in ("## Kennzahlen auf einen Blick", "## Das Jahr im Überblick", "## Stammstrecke")
    )
    assert "| ⌀ Verspätung (2026) | 3.2 min |" in lines[kennzahlen:overview]
    assert "| Juli | 1 | 3.3 min | 0 | 3.3 min | 0 | – |" in lines[overview:stammstrecke]
    below = lines[stammstrecke:]
    assert any("3.2 min" in line for line in below)
    assert not any("3.3 min" in line for line in below)
    assert all("│" in line for line in below if " min" in line)
    assert not any(line.startswith("|") and " min" in line for line in below)
    assert lines[overview + 2].endswith(ROUNDING)
