"""Tests for ``scripts/generate_markdown_stats.py``.

The aggregator is exercised end-to-end through CSV files written into a
``tmp_path`` directory — that mirrors the production read path exactly
(``csv.reader`` on real on-disk bytes) without coupling tests to any
mock-CSV abstraction. Where helpful, individual rendering helpers are
also called directly so a single regression in scaling logic shows up
as a focused failure rather than a giant whole-dashboard diff.
"""
from __future__ import annotations

import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import generate_markdown_stats as script  # noqa: E402
from src.utils import stats as stats_utils  # noqa: E402

VIENNA_TZ = ZoneInfo("Europe/Vienna")


# ---- Helpers ---------------------------------------------------------------


def _write_csv(
    path: Path,
    header: tuple[str, ...],
    rows: Sequence[tuple[str, ...]],
) -> None:
    """Helper that writes a small CSV the same way the production writer does.

    *rows* is typed as :class:`Sequence` rather than :class:`list` so
    callers can pass concrete tuple element types (e.g.
    ``list[tuple[str, str, str, str, str]]``) without tripping the
    list-invariance rule mypy enforces under ``--strict``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [",".join(header)]
    for row in rows:
        lines.append(",".join(row))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _stammstrecke_csv(tmp_path: Path, year: int = 2026) -> Path:
    return tmp_path / f"stammstrecke_{year}.csv"


def _stoerungen_csv(tmp_path: Path, year: int = 2026) -> Path:
    return tmp_path / f"stoerungen_{year}.csv"


# ---- Bar-rendering primitives ---------------------------------------------


def test_scale_bar_returns_zero_for_zero_value() -> None:
    assert script._scale_bar(0.0, 10.0) == 0


def test_scale_bar_returns_at_least_one_block_for_nonzero() -> None:
    """A non-zero value must always render as at least one block.

    Otherwise faint-but-present signals would visually disappear from
    the dashboard, defeating the purpose of the chart.
    """
    assert script._scale_bar(0.001, 10.0, width=24) == 1


def test_scale_bar_caps_at_width_for_value_at_max() -> None:
    assert script._scale_bar(10.0, 10.0, width=24) == 24


def test_scale_bar_handles_zero_max_safely() -> None:
    assert script._scale_bar(5.0, 0.0) == 0


def test_render_weekday_bars_includes_all_seven_days_even_when_empty_input(
) -> None:
    out = script.render_weekday_bars(
        {"Mo": 5, "Di": 0},
        glyph="🟦",
        title="Test",
    )
    body = "\n".join(out)
    for label in stats_utils.WEEKDAY_LABELS:
        assert label in body, f"weekday label {label} missing from rendered chart"
    assert "Test" in body


def test_render_hour_bars_renders_24_rows_for_nonempty_input() -> None:
    out = script.render_hour_bars({0: 1, 7: 5, 23: 2}, glyph="🟧", title="Stunden")
    body = "\n".join(out)
    for hour in (0, 7, 12, 23):
        assert f"{hour:02d}h" in body


def test_render_weekday_bars_handles_empty_data() -> None:
    out = script.render_weekday_bars({}, glyph="🟦", title="leer")
    body = "\n".join(out)
    assert "_Keine Daten verfügbar._" in body


# ---- Aggregation -----------------------------------------------------------


def test_aggregate_stammstrecke_counts_observations_and_threshold() -> None:
    """``threshold_exceedances`` counts each *row* whose persisted
    per-sample mean delay strictly exceeds the threshold — never more
    than once per cron cycle, so the same physical cycle is not
    multiplied into the count.
    """
    rows = [
        script.StammstreckeRow(
            timestamp=datetime(2026, 5, 4, 7, 0, tzinfo=VIENNA_TZ),
            weekday="Mo",
            hour=7,
            direction="Meidling",
            delay_minutes=4.0,
        ),
        script.StammstreckeRow(
            timestamp=datetime(2026, 5, 4, 8, 0, tzinfo=VIENNA_TZ),
            weekday="Mo",
            hour=8,
            direction="Meidling",
            delay_minutes=12.0,  # over threshold
        ),
        script.StammstreckeRow(
            timestamp=datetime(2026, 5, 5, 8, 0, tzinfo=VIENNA_TZ),
            weekday="Di",
            hour=8,
            direction="Floridsdorf",
            delay_minutes=11.0,  # over threshold
        ),
    ]
    agg = script.aggregate_stammstrecke(rows, threshold_minutes=9.0)
    assert agg.total_observations == 3
    assert agg.threshold_exceedances == 2
    assert agg.by_weekday_count == {"Mo": 2, "Di": 1}
    assert agg.by_hour_count == {7: 1, 8: 2}
    # average for Mo is (4 + 12) / 2 = 8
    assert agg.by_weekday_avg["Mo"] == pytest.approx(8.0)
    assert agg.by_direction == {"Meidling": 2, "Floridsdorf": 1}


def test_aggregate_ausfaelle_counts_per_dimension() -> None:
    """Cancellations aggregate by weekday, hour, direction, and line.

    Each :class:`AusfallRow` represents exactly one cancelled train
    (the writer deduplicates upstream via the pending-trip ledger),
    so the per-dimension counts MUST equal the row count along each
    axis. No averaging — cancellations are discrete events.
    """
    rows = [
        script.AusfallRow(
            timestamp=datetime(2026, 5, 4, 7, 30, tzinfo=VIENNA_TZ),
            weekday="Mo",
            hour=7,
            direction="Meidling",
            line="S1",
        ),
        script.AusfallRow(
            timestamp=datetime(2026, 5, 4, 8, 15, tzinfo=VIENNA_TZ),
            weekday="Mo",
            hour=8,
            direction="Meidling",
            line="S1",
        ),
        script.AusfallRow(
            timestamp=datetime(2026, 5, 5, 14, 0, tzinfo=VIENNA_TZ),
            weekday="Di",
            hour=14,
            direction="Praterstern",
            line="S2",
        ),
    ]
    agg = script.aggregate_ausfaelle(rows)
    assert agg.total_cancellations == 3
    assert agg.by_weekday == {"Mo": 2, "Di": 1}
    assert agg.by_hour == {7: 1, 8: 1, 14: 1}
    assert agg.by_direction == {"Meidling": 2, "Praterstern": 1}
    assert agg.by_line == {"S1": 2, "S2": 1}


def test_parse_ausfall_rows_skips_malformed_rows() -> None:
    """A row with an unparseable timestamp is silently dropped.

    Same corruption-tolerance contract as the other parsers: a single
    hand-edited bad row must never break the dashboard regeneration.
    """
    raw = [
        {
            "timestamp": "not-a-timestamp",
            "weekday": "Mo",
            "hour": "07",
            "direction": "Meidling",
            "line": "S1",
        },
        {
            "timestamp": "2026-05-04T07:30:00+02:00",
            "weekday": "Mo",
            "hour": "07",
            "direction": "Meidling",
            "line": "S1",
        },
    ]
    rows = script._parse_ausfall_rows(raw)
    assert len(rows) == 1
    assert rows[0].direction == "Meidling"
    assert rows[0].line == "S1"


def test_parse_ausfall_rows_uses_fallbacks_for_missing_fields() -> None:
    """Empty direction/line round-trip as the explicit ``unbekannt`` sentinel."""
    raw = [
        {
            "timestamp": "2026-05-04T07:30:00+02:00",
            "weekday": "",
            "hour": "",
            "direction": "",
            "line": "",
        },
    ]
    rows = script._parse_ausfall_rows(raw)
    assert len(rows) == 1
    assert rows[0].direction == "unbekannt"
    assert rows[0].line == "unbekannt"
    assert rows[0].weekday == "Mo"  # 2026-05-04 is a Monday
    assert rows[0].hour == 7


def test_aggregate_stoerungen_counts_per_dimension() -> None:
    rows = [
        script.StoerungRow(
            timestamp=datetime(2026, 5, 4, 7, 0, tzinfo=VIENNA_TZ),
            weekday="Mo",
            hour=7,
            provider="ÖBB",
            location_name="Wien Floridsdorf",
        ),
        script.StoerungRow(
            timestamp=datetime(2026, 5, 4, 14, 0, tzinfo=VIENNA_TZ),
            weekday="Mo",
            hour=14,
            provider="Wiener Linien",
            location_name="Karlsplatz",
        ),
        script.StoerungRow(
            timestamp=datetime(2026, 5, 5, 14, 0, tzinfo=VIENNA_TZ),
            weekday="Di",
            hour=14,
            provider="ÖBB",
            location_name="Wien Floridsdorf",
        ),
    ]
    agg = script.aggregate_stoerungen(rows)
    assert agg.total_disruptions == 3
    assert agg.by_provider == {"ÖBB": 2, "Wiener Linien": 1}


# ---- CSV reading -----------------------------------------------------------


def test_collect_year_data_reads_well_formed_files(tmp_path: Path) -> None:
    _write_csv(
        _stammstrecke_csv(tmp_path),
        stats_utils.STAMMSTRECKE_HEADER,
        [
            ("2026-05-04T07:30:00+02:00", "Mo", "07", "Meidling", "5.50"),
            ("2026-05-04T08:00:00+02:00", "Mo", "08", "Meidling", "12.00"),
        ],
    )
    _write_csv(
        _stoerungen_csv(tmp_path),
        stats_utils.STOERUNGEN_HEADER,
        [
            ("2026-05-04T07:30:00+02:00", "Mo", "07", "ÖBB", "Wien Floridsdorf"),
            ("2026-05-04T08:00:00+02:00", "Mo", "08", "Wiener Linien", "Karlsplatz"),
        ],
    )
    sm, st, au = script.collect_year_data(2026, stats_dir=tmp_path)
    assert len(sm) == 2
    assert len(st) == 2
    # No ausfaelle CSV in this test → empty list (the parser tolerates
    # a missing file the same way it tolerates a missing
    # stammstrecke/stoerungen file).
    assert au == []
    assert sm[0].direction == "Meidling"
    assert st[0].location_name == "Wien Floridsdorf"


def test_collect_year_data_returns_empty_when_files_missing(tmp_path: Path) -> None:
    sm, st, au = script.collect_year_data(2026, stats_dir=tmp_path / "nope")
    assert sm == []
    assert st == []
    assert au == []


def test_collect_year_data_skips_oversized_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A planted-huge CSV must be skipped, not buffered into memory."""
    monkeypatch.setattr(script, "MAX_CSV_BYTES", 64)
    payload_rows = [
        ("2026-05-04T07:30:00+02:00", "Mo", "07", "Meidling", "5.50"),
    ] * 10
    _write_csv(_stammstrecke_csv(tmp_path), stats_utils.STAMMSTRECKE_HEADER, payload_rows)
    sm, _, _ = script.collect_year_data(2026, stats_dir=tmp_path)
    assert sm == []


def test_collect_year_data_rejects_unexpected_header(tmp_path: Path) -> None:
    _write_csv(
        _stammstrecke_csv(tmp_path),
        ("not", "the", "expected", "header", "row"),
        [("a", "b", "c", "d", "e")],
    )
    sm, _, _ = script.collect_year_data(2026, stats_dir=tmp_path)
    assert sm == []


def test_parse_stammstrecke_rows_skips_malformed_rows() -> None:
    raw = [
        # Bad timestamp.
        {
            "timestamp": "not-a-timestamp",
            "weekday": "Mo",
            "hour": "07",
            "direction": "Meidling",
            "delay_minutes": "5.0",
        },
        # Bad delay value.
        {
            "timestamp": "2026-05-04T07:30:00+02:00",
            "weekday": "Mo",
            "hour": "07",
            "direction": "Meidling",
            "delay_minutes": "not-a-number",
        },
        # Good row — the survivor.
        {
            "timestamp": "2026-05-04T07:30:00+02:00",
            "weekday": "Mo",
            "hour": "07",
            "direction": "Meidling",
            "delay_minutes": "5.0",
        },
    ]
    rows = script._parse_stammstrecke_rows(raw)
    assert len(rows) == 1
    assert rows[0].direction == "Meidling"


def test_parse_stoerung_rows_uses_fallbacks_for_missing_fields() -> None:
    raw = [
        {
            "timestamp": "2026-05-04T07:30:00+02:00",
            "weekday": "",
            "hour": "",
            "provider": "",
            "location_name": "",
        },
    ]
    rows = script._parse_stoerung_rows(raw)
    assert len(rows) == 1
    assert rows[0].provider == "unbekannt"
    assert rows[0].location_name == "unbekannt"
    assert rows[0].weekday == "Mo"  # 2026-05-04 is a Monday
    assert rows[0].hour == 7


# ---- Full markdown rendering ----------------------------------------------


def test_render_markdown_includes_summary_table_and_sections() -> None:
    sm_agg = script.StammstreckeAggregate(
        by_weekday_count={"Mo": 1},
        by_weekday_avg={"Mo": 5.0},
        by_hour_count={7: 1},
        by_hour_avg={7: 5.0},
        by_direction={"Meidling": 1},
        total_observations=1,
        threshold_exceedances=0,
        threshold_minutes=9.0,
    )
    st_agg = script.StoerungAggregate(
        by_weekday={"Mo": 1},
        by_hour={7: 1},
        by_provider={"ÖBB": 1},
        total_disruptions=1,
    )
    md = script.render_markdown(
        year=2026,
        generated_at=datetime(2026, 5, 9, 8, 30, tzinfo=VIENNA_TZ),
        stammstrecke=sm_agg,
        stoerungen=st_agg,
        ausfaelle=script.AusfallAggregate(),
    )
    assert md.startswith("# Wien ÖPNV — Statistik 2026")
    assert "## Stammstrecke" in md
    assert "## Ausfälle" in md
    assert "## Störungen" in md
    assert "ÖBB" in md
    assert md.endswith("\n")


def test_render_markdown_is_byte_stable_across_repeat_calls() -> None:
    """Two invocations on the same inputs must produce identical bytes.

    The auto-commit step in the workflow only commits when the output
    actually changed, so non-determinism here would create churn in
    the git history.
    """
    sm_agg = script.StammstreckeAggregate(
        by_weekday_count={"Mo": 2, "Di": 2},
        by_weekday_avg={"Mo": 6.0, "Di": 7.0},
        by_hour_count={7: 2, 8: 2},
        by_hour_avg={7: 6.0, 8: 7.0},
        by_direction={"Meidling": 2, "Floridsdorf": 2},
        total_observations=4,
        threshold_exceedances=1,
    )
    st_agg = script.StoerungAggregate(
        by_weekday={"Mo": 2, "Di": 1},
        by_hour={7: 1, 8: 2},
        by_provider={"ÖBB": 2, "Wiener Linien": 1},
        total_disruptions=3,
    )
    when = datetime(2026, 5, 9, 8, 30, tzinfo=VIENNA_TZ)
    au_agg = script.AusfallAggregate(
        by_weekday={"Mo": 1},
        by_hour={7: 1},
        by_direction={"Meidling": 1},
        by_line={"S1": 1},
        total_cancellations=1,
    )
    a = script.render_markdown(
        year=2026,
        generated_at=when,
        stammstrecke=sm_agg,
        stoerungen=st_agg,
        ausfaelle=au_agg,
    )
    b = script.render_markdown(
        year=2026,
        generated_at=when,
        stammstrecke=sm_agg,
        stoerungen=st_agg,
        ausfaelle=au_agg,
    )
    assert a == b


def test_render_markdown_handles_empty_aggregates_gracefully() -> None:
    md = script.render_markdown(
        year=2026,
        generated_at=datetime(2026, 5, 9, 8, 30, tzinfo=VIENNA_TZ),
        stammstrecke=script.StammstreckeAggregate(),
        stoerungen=script.StoerungAggregate(),
        ausfaelle=script.AusfallAggregate(),
    )
    assert "_Keine Daten verfügbar._" in md
    # The cancellations section renders explicit "_Keine Ausfälle erfasst._"
    # placeholders for both the directions table and the lines table —
    # an unconditional zero is the operationally-meaningful signal.
    assert "_Keine Ausfälle erfasst._" in md


def test_render_markdown_global_avg_uses_observation_weighted_mean() -> None:
    """``⌀ Verspätung ({year})`` is the year-wide observation-weighted
    micro-average over every persisted row of the calendar year.

    Multiplying the per-weekday mean by the per-weekday observation
    count and dividing by the total recovers the original delay sum
    exactly — no separate iteration over the raw rows is needed and
    the same row never contributes twice. Anti-regression: the
    earlier ``fmean``-over-per-weekday-means approach silently
    macro-averaged (a Saturday with 3 obs counted as much as a Sunday
    with 23), which produced visibly wrong values when the daily
    distribution wasn't uniform.
    """
    sm_agg = script.StammstreckeAggregate(
        by_weekday_count={"Sa": 3, "So": 23},
        by_weekday_avg={"Sa": 2.0 / 3.0, "So": 4.0 / 23.0},
        by_hour_count={},
        by_hour_avg={},
        by_direction={"Meidling": 15, "Floridsdorf": 11},
        total_observations=26,
        threshold_exceedances=0,
        threshold_minutes=9.0,
    )
    md = script.render_markdown(
        year=2026,
        generated_at=datetime(2026, 5, 10, 16, 35, tzinfo=VIENNA_TZ),
        stammstrecke=sm_agg,
        stoerungen=script.StoerungAggregate(),
        ausfaelle=script.AusfallAggregate(),
    )
    # 3*(2/3) + 23*(4/23) = 6.0 → 6.0/26 ≈ 0.231 → "0.2 min"
    assert "| ⌀ Verspätung (2026) | 0.2 min |" in md
    # Anti-regression: the previous macro-average rendered "0.4 min".
    assert "| ⌀ Verspätung (2026) | 0.4 min |" not in md


def test_render_markdown_summary_includes_cancellation_count() -> None:
    """The summary table surfaces the year-wide cancellation count.

    Operators reading the top of the dashboard MUST see the number
    of cancellations at a glance, side-by-side with the other
    Stammstrecke headline metrics.
    """
    au_agg = script.AusfallAggregate(
        by_weekday={"Mo": 1, "Di": 2},
        by_hour={7: 1, 14: 2},
        by_direction={"Meidling": 2, "Praterstern": 1},
        by_line={"S1": 2, "S2": 1},
        total_cancellations=3,
    )
    md = script.render_markdown(
        year=2026,
        generated_at=datetime(2026, 5, 9, 8, 30, tzinfo=VIENNA_TZ),
        stammstrecke=script.StammstreckeAggregate(),
        stoerungen=script.StoerungAggregate(),
        ausfaelle=au_agg,
    )
    assert "| Stammstrecke-Ausfälle (2026) | 3 |" in md


def test_render_markdown_ausfalle_section_renders_direction_and_line_tables() -> None:
    """The Ausfälle section lists per-direction and per-line counts.

    Both breakdowns surface in the dashboard so operators can spot
    "S1 fails twice as often as S2" or "Meidling-bound trains cancel
    more often than Praterstern-bound" without needing to scrape the
    raw CSV.
    """
    au_agg = script.AusfallAggregate(
        by_weekday={"Mo": 1, "Di": 2},
        by_hour={7: 1, 14: 2},
        by_direction={"Meidling": 2, "Praterstern": 1},
        by_line={"S1": 2, "S2": 1},
        total_cancellations=3,
    )
    md = script.render_markdown(
        year=2026,
        generated_at=datetime(2026, 5, 9, 8, 30, tzinfo=VIENNA_TZ),
        stammstrecke=script.StammstreckeAggregate(),
        stoerungen=script.StoerungAggregate(),
        ausfaelle=au_agg,
    )
    assert "### Ausfälle je Richtung" in md
    assert "### Ausfälle je Linie" in md
    # The direction table renders both directions with their counts.
    assert "| Meidling | 2 |" in md
    assert "| Praterstern | 1 |" in md
    # The line table renders both lines with their counts.
    assert "| S1 | 2 |" in md
    assert "| S2 | 1 |" in md


def test_render_readme_ausfaelle_live_block_zero_renders_explicit_zero() -> None:
    """The live block renders ``0`` (not a placeholder) when no cancellations.

    Anti-regression: an empty-data branch that swallowed the row would
    conflate "stable service" with "data missing" in the README. The
    block must always show a definitive count so operators can tell
    the two states apart at a glance.
    """
    now = datetime(2026, 5, 9, 8, 30, tzinfo=VIENNA_TZ)
    body = script.render_readme_ausfaelle_live_block([], now=now)
    assert "| Ausfälle (gesamt) | 0 |" in body
    assert "Letzte Aktualisierung" in body


def test_render_readme_ausfaelle_block_renders_top_lines() -> None:
    """The 30-day block surfaces the top 3 most-cancelled lines.

    Provides at-a-glance operator signal in the README without
    requiring a click through to the full dashboard. Sorted by count
    descending so the most-cancelled line shows first; tie-broken by
    line name for a stable render.
    """
    now = datetime(2026, 5, 9, 8, 30, tzinfo=VIENNA_TZ)
    rows = [
        script.AusfallRow(
            timestamp=datetime(2026, 5, 1, 7, 30, tzinfo=VIENNA_TZ),
            weekday="Fr",
            hour=7,
            direction="Meidling",
            line="S1",
        ),
        script.AusfallRow(
            timestamp=datetime(2026, 5, 2, 7, 30, tzinfo=VIENNA_TZ),
            weekday="Sa",
            hour=7,
            direction="Meidling",
            line="S1",
        ),
        script.AusfallRow(
            timestamp=datetime(2026, 5, 3, 7, 30, tzinfo=VIENNA_TZ),
            weekday="So",
            hour=7,
            direction="Praterstern",
            line="S2",
        ),
    ]
    body = script.render_readme_ausfaelle_block(rows, now=now)
    assert "| Ausfälle (gesamt) | 3 |" in body
    # S1 appears twice, S2 once → S1 wins.
    assert "S1 (2)" in body
    assert "S2 (1)" in body


def test_render_markdown_summary_avg_renders_placeholder_when_empty() -> None:
    """An empty Stammstrecke aggregate must render the avg cell as
    ``_keine Daten_`` rather than ``0.0 min`` — the misleading zero
    would hide the data-absent state from operators eyeballing the
    dashboard."""
    md = script.render_markdown(
        year=2026,
        generated_at=datetime(2026, 5, 10, 16, 35, tzinfo=VIENNA_TZ),
        stammstrecke=script.StammstreckeAggregate(),
        stoerungen=script.StoerungAggregate(),
        ausfaelle=script.AusfallAggregate(),
    )
    assert "| ⌀ Verspätung (2026) | _keine Daten_ |" in md


# ---- write_dashboard ------------------------------------------------------


def test_write_dashboard_produces_atomic_output(tmp_path: Path) -> None:
    out = tmp_path / "subdir" / "statistik.md"
    script.write_dashboard("# hi\n", output_path=out)
    assert out.read_text(encoding="utf-8") == "# hi\n"


# ---- main(): integration --------------------------------------------------


def test_main_writes_dashboard_for_well_formed_inputs(tmp_path: Path) -> None:
    _write_csv(
        _stammstrecke_csv(tmp_path),
        stats_utils.STAMMSTRECKE_HEADER,
        [
            ("2026-05-04T07:30:00+02:00", "Mo", "07", "Meidling", "5.50"),
            ("2026-05-04T08:00:00+02:00", "Mo", "08", "Floridsdorf", "11.00"),
        ],
    )
    _write_csv(
        _stoerungen_csv(tmp_path),
        stats_utils.STOERUNGEN_HEADER,
        [
            ("2026-05-04T07:30:00+02:00", "Mo", "07", "ÖBB", "Wien Floridsdorf"),
        ],
    )
    output = tmp_path / "statistik.md"
    # ``--skip-readme`` is critical here: the argparse default for
    # ``--readme-path`` is ``DEFAULT_README_PATH`` (the production
    # ``REPO_ROOT/README.md``). A test that omits the flag overwrites
    # the real repository README with whatever this synthetic stats
    # window renders — exactly the bug that contaminated the committed
    # README on 2026-05-09 (PR #1397). Either ``--skip-readme`` or
    # an explicit ``--readme-path`` pointing at ``tmp_path`` is
    # mandatory for any ``main()`` test.
    rc = script.main(
        [
            "--year", "2026",
            "--stats-dir", str(tmp_path),
            "--output", str(output),
            "--skip-readme",
        ]
    )
    assert rc == 0
    body = output.read_text(encoding="utf-8")
    assert "Stammstrecke" in body
    assert "Meidling" in body


def test_main_returns_zero_with_no_input_files(tmp_path: Path) -> None:
    output = tmp_path / "statistik.md"
    rc = script.main(
        [
            "--year", "2026",
            "--stats-dir", str(tmp_path / "missing"),
            "--output", str(output),
            "--skip-readme",
        ]
    )
    assert rc == 0
    body = output.read_text(encoding="utf-8")
    assert "Keine Daten verfügbar" in body
