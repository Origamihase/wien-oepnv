"""The section "Das Jahr im Überblick": empty inputs, partial years and recorded months.

A year without rows or with one series only, and which months count as
recorded when the series start and end at different times.
"""

from __future__ import annotations

from src.markdown_stats.year_stats import render_year_overview
from tests._year_stats_support import (
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
    # March has delay rows: the monitor ran and no train was cancelled.
    assert [row[5] for row in _table(page, MONTHS)] == ["–", "–", "0"] + ["–"] * 9
    assert [row[6] for row in _table(page, MONTHS)] == ["–", "–", "1"] + ["–"] * 9


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
