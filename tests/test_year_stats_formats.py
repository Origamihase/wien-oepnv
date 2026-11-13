"""The section "Das Jahr im Überblick": threshold, number formats, rounding and escaping.

How a delay and a count are written, how a delay is rounded (half up, as by
hand), what the page says about its rounding, and that a text of the ledger
cannot break a table.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.markdown_stats import year_stats
from src.markdown_stats.rows import collect_year_data
from src.markdown_stats.year_archive import render_year_archive
from src.markdown_stats.year_stats import render_year_overview
from src.utils.stats import to_vienna
from tests._year_stats_support import (
    AUSFALL_DAYS,
    DELAY_DAYS,
    DIRECTIONS,
    MONTHS,
    ROOT,
    _at,
    _au,
    _day_of_delays,
    _exact,
    _half_up,
    _header,
    _sm,
    _table,
)


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
    # The mean is exactly -1.15: a half is rounded away from zero.
    assert _table(page, MONTHS)[2][:5] == ["März", "20", "-1.2 min", "0", "0.0 min"]


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


# ---- Second round of the review of 2026-11-10 -------------------------------

ROUNDING = (
    "Die Verspätungen dieses Abschnitts sind auf eine Nachkommastelle kaufmännisch "
    "gerundet; „Kennzahlen auf einen Blick“ und die Balkendiagramme weiter unten runden "
    "wie das Dashboard."
)


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
