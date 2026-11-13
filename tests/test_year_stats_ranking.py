"""The section "Das Jahr im Überblick": the order and the cut of the ranked tables.

Which day or direction comes first on a tie, where the tables are cut, and
what the notes under them say (also for a count of one).
"""

from __future__ import annotations

import math
from fractions import Fraction

import pytest

from src.markdown_stats.year_stats import (
    MAX_DIRECTION_ROWS,
    MIN_DAY_OBSERVATIONS,
    TOP_DAYS,
    render_year_overview,
)
from tests._year_stats_support import (
    AUSFALL_DAYS,
    DELAY_DAYS,
    DIRECTIONS,
    MONTHS,
    _at,
    _au,
    _day_of_delays,
    _exact,
    _section,
    _sm,
    _table,
)


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
