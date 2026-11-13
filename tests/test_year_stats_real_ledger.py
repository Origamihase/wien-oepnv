"""The section "Das Jahr im Überblick" from the real ledger in ``data/stats`` (read only)."""

from __future__ import annotations

import pytest

from src.feed.stammstrecke import DELAY_THRESHOLD_MINUTES
from src.markdown_stats.rows import collect_year_data
from src.markdown_stats.year_stats import (
    MAX_DIRECTION_ROWS,
    MONTH_NAMES,
    NOT_RECORDED,
    TOP_DAYS,
    render_year_overview,
)
from tests._year_stats_support import (
    AUSFALL_DAYS,
    DELAY_DAYS,
    DIRECTIONS,
    MONTHS,
    PERIOD,
    ROOT,
    _table,
)


# ---- The real ledger (read only) -------------------------------------------


def _cell_count(cell: str) -> int:
    return 0 if cell == NOT_RECORDED else int(cell)


def test_real_ledger_renders_and_its_months_add_up() -> None:
    """Smoke test on ``data/stats`` as committed; it reads and writes nothing.

    Skipped in a checkout without the 2026 ledgers.
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
