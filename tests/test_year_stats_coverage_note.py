"""The section "Das Jahr im Überblick": the coverage note at its head.

Where the note of ``render_finished_year_coverage_note`` stands in the
section and what an empty note leaves.
"""

from __future__ import annotations

import pytest

from src.markdown_stats.direction_coverage import render_finished_year_coverage_note
from src.markdown_stats.year_stats import render_year_overview
from tests._year_stats_support import (
    DIRECTIONS,
    PERIOD,
    _day_of_delays,
    _table,
)


NOTE = (
    "> ⚠️ **Eingeschränkte Abdeckung:** Von Wien Hbf Richtung **Praterstern** sind auf "
    "der Stammstrecke Fahrten nur bis 14.08.2026 erfasst. Die Jahreszahlen unten "
    "enthalten Richtung **Praterstern** daher nur bis dahin.\n\n"
)


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
