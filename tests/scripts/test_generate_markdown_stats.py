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
    sm, st = script.collect_year_data(2026, stats_dir=tmp_path)
    assert len(sm) == 2
    assert len(st) == 2
    assert sm[0].direction == "Meidling"
    assert st[0].location_name == "Wien Floridsdorf"


def test_collect_year_data_returns_empty_when_files_missing(tmp_path: Path) -> None:
    sm, st = script.collect_year_data(2026, stats_dir=tmp_path / "nope")
    assert sm == []
    assert st == []


def test_collect_year_data_skips_oversized_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A planted-huge CSV must be skipped, not buffered into memory."""
    monkeypatch.setattr(script, "MAX_CSV_BYTES", 64)
    payload_rows = [
        ("2026-05-04T07:30:00+02:00", "Mo", "07", "Meidling", "5.50"),
    ] * 10
    _write_csv(_stammstrecke_csv(tmp_path), stats_utils.STAMMSTRECKE_HEADER, payload_rows)
    sm, _ = script.collect_year_data(2026, stats_dir=tmp_path)
    assert sm == []


def test_collect_year_data_rejects_unexpected_header(tmp_path: Path) -> None:
    _write_csv(
        _stammstrecke_csv(tmp_path),
        ("not", "the", "expected", "header", "row"),
        [("a", "b", "c", "d", "e")],
    )
    sm, _ = script.collect_year_data(2026, stats_dir=tmp_path)
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
    )
    assert md.startswith("# Wien ÖPNV — Statistik 2026")
    assert "## Stammstrecke" in md
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
    a = script.render_markdown(year=2026, generated_at=when, stammstrecke=sm_agg, stoerungen=st_agg)
    b = script.render_markdown(year=2026, generated_at=when, stammstrecke=sm_agg, stoerungen=st_agg)
    assert a == b


def test_render_markdown_handles_empty_aggregates_gracefully() -> None:
    md = script.render_markdown(
        year=2026,
        generated_at=datetime(2026, 5, 9, 8, 30, tzinfo=VIENNA_TZ),
        stammstrecke=script.StammstreckeAggregate(),
        stoerungen=script.StoerungAggregate(),
    )
    assert "_Keine Daten verfügbar._" in md


def test_render_markdown_global_avg_uses_observation_weighted_mean() -> None:
    """``⌀ Verspätung (alle Tage)`` must equal the README's micro-average,
    not an unweighted ``fmean`` over the per-weekday means.

    The empirical distribution Sa(3 obs avg 2/3 min) and So(23 obs avg
    4/23 min) is the regression that motivated the fix on branch
    ``claude/fix-delay-statistics``: the broken macro-average rendered
    ``0.4 min`` while the README cell — computed from the raw rows —
    showed ``0.2 min``. Pinning both rendering paths to the same
    observation-weighted mean closes that drift.
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
    )
    # 3*(2/3) + 23*(4/23) = 6.0 → 6.0/26 ≈ 0.231 → "0.2 min"
    assert "| ⌀ Verspätung (alle Tage) | 0.2 min |" in md
    # Anti-regression: the previous macro-average rendered "0.4 min".
    assert "| ⌀ Verspätung (alle Tage) | 0.4 min |" not in md


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
