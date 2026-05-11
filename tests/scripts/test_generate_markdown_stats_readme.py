"""Tests for the README-snapshot patcher in ``generate_markdown_stats.py``.

The patcher is a separate write boundary from the
``docs/statistik.md`` dashboard:

* The dashboard is full-history, byte-identical for the same input,
  and consumed by humans via the docs site.
* The README block is a 30-day rolling snapshot that GitHub renders on
  the public landing page; it is patched **in place** between named
  ``<!-- STATS:* -->`` markers so the surrounding hand-authored README
  content survives every workflow run untouched.

These tests guard the boundary on three axes that the dashboard tests
do not cover:

1. **Idempotency** — a second run with identical input must not touch
   the file (mtime preservation matters because the auto-commit action
   uses content equality, not timestamps).
2. **Marker invariants** — missing markers, a single half of a pair,
   markers inside a fenced code block, and unicode spacing between
   markers must all leave the surrounding README byte-stable.
3. **Markdown-injection at the render boundary** — the ``location_name``
   column is interpolated into a 3-column table; a CSV row carrying a
   literal ``|`` / ``` ` ``` / ``<`` must not break out of the cell.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import generate_markdown_stats as script  # noqa: E402

VIENNA_TZ = ZoneInfo("Europe/Vienna")
NOW = datetime(2026, 5, 9, 12, 0, tzinfo=VIENNA_TZ)


def _make_stam(
    delay_minutes: float,
    *,
    timestamp: datetime,
    direction: str = "Wien Hbf -> Floridsdorf",
) -> script.StammstreckeRow:
    return script.StammstreckeRow(
        timestamp=timestamp,
        weekday="Mo",
        hour=timestamp.hour,
        direction=direction,
        delay_minutes=delay_minutes,
    )


def _readme_with_markers(extra: str = "") -> str:
    """Return a minimal README scaffold containing the Stammstrecke marker pair.

    The surrounding text is intentionally non-trivial so a flawed
    patcher that rewrites more than the marker contents shows up as a
    diff in the post-condition assertions.
    """
    return (
        "# Wien ÖPNV Feed\n"
        "\n"
        "Some hand-authored intro paragraph that must not be touched.\n"
        "\n"
        "<!-- STATS:STAMMSTRECKE:BEGIN -->\n"
        "_Platzhalter Stammstrecke._\n"
        "<!-- STATS:STAMMSTRECKE:END -->\n"
        "\n"
        f"Trailing user-authored content. {extra}\n"
    )


# ---- Window filter ---------------------------------------------------------


def test_filter_rows_by_window_includes_within_cutoff() -> None:
    rows = [
        _make_stam(5.0, timestamp=NOW - timedelta(days=29)),
        _make_stam(7.0, timestamp=NOW - timedelta(hours=1)),
    ]
    filtered = script._filter_rows_by_window(rows, days=30, now=NOW)
    assert len(filtered) == 2


def test_filter_rows_by_window_excludes_before_cutoff() -> None:
    rows = [
        _make_stam(5.0, timestamp=NOW - timedelta(days=31)),
        _make_stam(7.0, timestamp=NOW - timedelta(days=29, hours=23)),
    ]
    filtered = script._filter_rows_by_window(rows, days=30, now=NOW)
    assert len(filtered) == 1
    assert filtered[0].delay_minutes == 7.0


def test_filter_rows_by_window_zero_or_negative_returns_empty() -> None:
    rows = [_make_stam(5.0, timestamp=NOW)]
    assert script._filter_rows_by_window(rows, days=0, now=NOW) == []
    assert script._filter_rows_by_window(rows, days=-1, now=NOW) == []


# ---- Stammstrecke render --------------------------------------------------


def test_render_stammstrecke_block_with_data_uses_mean_and_count() -> None:
    rows = [
        _make_stam(10.0, timestamp=NOW),
        _make_stam(5.0, timestamp=NOW),
        _make_stam(12.0, timestamp=NOW),
    ]
    block = script.render_readme_stammstrecke_block(
        rows, now=NOW, window_days=30
    )
    assert "| Beobachtungen (gesamt) | 3 |" in block
    # mean of [10, 5, 12] is 27/3 = 9.0 — README displays the
    # arithmetic mean across the window.
    assert "| Durchschnittliche Verspätung | 9.0 min |" in block
    # Count of rows whose per-sample mean is > 9 min: [10, 12] = 2.
    # Each row counts at most once — the same physical cron cycle is
    # never multiplied into the threshold counter.
    assert "| Kritische Verspätungen (> 9 min) | 2 |" in block
    assert "| Letzte Aktualisierung | 2026-05-09 12:00" in block
    # Closing newline so the END marker stays on its own line
    assert block.endswith("\n")


def test_render_stammstrecke_block_empty_uses_pending_placeholder() -> None:
    block = script.render_readme_stammstrecke_block(
        [], now=NOW, window_days=30
    )
    assert script.README_PENDING_PLACEHOLDER in block
    assert "| Letzte Aktualisierung | 2026-05-09 12:00" in block


def test_render_stammstrecke_block_uses_german_thousands_separator() -> None:
    rows = [_make_stam(1.0, timestamp=NOW) for _ in range(1234)]
    block = script.render_readme_stammstrecke_block(
        rows, now=NOW, window_days=30
    )
    assert "| Beobachtungen (gesamt) | 1.234 |" in block
    # No comma-separated number leaked from str.format
    assert "1,234" not in block


def test_render_stammstrecke_block_threshold_label_for_integer() -> None:
    # The threshold defaults to 9.0 — the label drops the trailing zero.
    rows = [_make_stam(5.0, timestamp=NOW)]
    block = script.render_readme_stammstrecke_block(
        rows, now=NOW, window_days=30
    )
    assert "(> 9 min)" in block


def test_render_stammstrecke_block_threshold_label_for_fractional() -> None:
    rows = [_make_stam(5.0, timestamp=NOW)]
    block = script.render_readme_stammstrecke_block(
        rows, now=NOW, window_days=30, threshold_minutes=4.5
    )
    assert "(> 4.5 min)" in block


# ---- README patcher --------------------------------------------------------


def test_patch_readme_replaces_marker_content(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text(_readme_with_markers(), encoding="utf-8")
    sections = {"STAMMSTRECKE": "STAMMSTRECKE BODY\n"}
    changed = script.patch_readme_stats(readme, sections)
    assert changed is True
    new_text = readme.read_text(encoding="utf-8")
    assert "STAMMSTRECKE BODY" in new_text
    # Surrounding hand-authored content survives byte-stable.
    assert "Some hand-authored intro paragraph that must not be touched." in new_text
    assert "Trailing user-authored content." in new_text
    # Marker pair still present and intact.
    assert "<!-- STATS:STAMMSTRECKE:BEGIN -->" in new_text
    assert "<!-- STATS:STAMMSTRECKE:END -->" in new_text


def test_patch_readme_idempotent_with_same_input(tmp_path: Path) -> None:
    """A second run with identical sections must not rewrite the file."""
    readme = tmp_path / "README.md"
    readme.write_text(_readme_with_markers(), encoding="utf-8")
    sections = {"STAMMSTRECKE": "STAMMSTRECKE BODY\n"}
    assert script.patch_readme_stats(readme, sections) is True
    snapshot = readme.read_bytes()
    # Second call must report "no change" AND leave the file byte-stable.
    assert script.patch_readme_stats(readme, sections) is False
    assert readme.read_bytes() == snapshot


def test_patch_readme_missing_marker_logs_warning_and_returns_false(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("# README\nno markers here\n", encoding="utf-8")
    snapshot = readme.read_bytes()
    sections = {"STAMMSTRECKE": "BODY\n"}
    with caplog.at_level(logging.WARNING, logger=script.LOGGER.name):
        result = script.patch_readme_stats(readme, sections)
    assert result is False
    assert readme.read_bytes() == snapshot
    assert any(
        "STATS:STAMMSTRECKE" in record.getMessage()
        and "nicht gefunden" in record.getMessage()
        for record in caplog.records
    )


def test_patch_readme_partial_markers_skips_only_missing_section(
    tmp_path: Path,
) -> None:
    """One section name has no corresponding marker pair: the available
    section is patched, the missing one is logged and skipped without
    touching the rest."""
    readme = tmp_path / "README.md"
    readme.write_text(
        "# README\n"
        "<!-- STATS:STAMMSTRECKE:BEGIN -->\n"
        "_old_\n"
        "<!-- STATS:STAMMSTRECKE:END -->\n"
        "no other markers here\n",
        encoding="utf-8",
    )
    sections = {
        "STAMMSTRECKE": "FRESH STAMM\n",
        "MISSING": "FRESH MISSING\n",
    }
    assert script.patch_readme_stats(readme, sections) is True
    new_text = readme.read_text(encoding="utf-8")
    assert "FRESH STAMM" in new_text
    assert "FRESH MISSING" not in new_text
    assert "no other markers here" in new_text


def test_patch_readme_oversize_returns_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An oversized README must be treated as missing — the dashboard
    pipeline still succeeds, the operator sees the warning."""
    readme = tmp_path / "README.md"
    readme.write_text(_readme_with_markers(extra="x" * 100), encoding="utf-8")
    snapshot = readme.read_bytes()
    monkeypatch.setattr(script, "README_MAX_BYTES", 10)  # ridiculously low
    result = script.patch_readme_stats(readme, {"STAMMSTRECKE": "X\n"})
    assert result is False
    assert readme.read_bytes() == snapshot


def test_patch_readme_missing_file_returns_false(tmp_path: Path) -> None:
    readme = tmp_path / "does-not-exist.md"
    result = script.patch_readme_stats(readme, {"STAMMSTRECKE": "X\n"})
    assert result is False
    assert not readme.exists()


def test_patch_readme_preserves_marker_lines_byte_for_byte(
    tmp_path: Path,
) -> None:
    """The BEGIN / END marker lines themselves must NEVER change.

    A workflow that writes ``<!-- STATS:NAME:BEGIN-->`` (no space) or
    omits the trailing newline would silently break the next run's
    matcher. This test pins the exact bytes the patcher emits.
    """
    readme = tmp_path / "README.md"
    readme.write_text(_readme_with_markers(), encoding="utf-8")
    script.patch_readme_stats(readme, {"STAMMSTRECKE": "X\n"})
    new_text = readme.read_text(encoding="utf-8")
    # The marker lines are still on their own line, with the canonical
    # spacing the regex matches.
    assert "\n<!-- STATS:STAMMSTRECKE:BEGIN -->\n" in new_text
    assert "\n<!-- STATS:STAMMSTRECKE:END -->\n" in new_text


# ---- main() smoke tests ---------------------------------------------------


def _seed_csvs(stats_dir: Path, *, year: int = 2026) -> None:
    """Write the minimum two CSVs ``main()`` needs to render a non-empty
    dashboard.
    """
    stats_dir.mkdir(parents=True, exist_ok=True)
    (stats_dir / f"stammstrecke_{year}.csv").write_text(
        ",".join(("timestamp", "weekday", "hour", "direction", "delay_minutes"))
        + "\n"
        + "2026-05-09T08:00:00+02:00,Sa,8,Wien Hbf->Floridsdorf,12.0\n"
        + "2026-05-09T08:30:00+02:00,Sa,8,Wien Hbf->Floridsdorf,5.0\n",
        encoding="utf-8",
    )
    (stats_dir / f"stoerungen_{year}.csv").write_text(
        ",".join(("timestamp", "weekday", "hour", "provider", "location_name"))
        + "\n"
        + "2026-05-09T08:00:00+02:00,Sa,8,wl,Wien Hbf\n"
        + "2026-05-09T08:30:00+02:00,Sa,8,wl,Wien Hbf\n"
        + "2026-05-09T09:00:00+02:00,Sa,9,oebb,Floridsdorf\n",
        encoding="utf-8",
    )


def test_main_writes_readme_with_30_day_window(tmp_path: Path) -> None:
    stats_dir = tmp_path / "stats"
    _seed_csvs(stats_dir)
    readme = tmp_path / "README.md"
    readme.write_text(_readme_with_markers(), encoding="utf-8")
    output = tmp_path / "statistik.md"

    rc = script.main(
        [
            "--year",
            "2026",
            "--stats-dir",
            str(stats_dir),
            "--output",
            str(output),
            "--readme-path",
            str(readme),
            "--readme-window-days",
            "30",
            "--now-iso",
            "2026-05-09T12:00:00+02:00",
        ]
    )
    assert rc == 0
    new_text = readme.read_text(encoding="utf-8")
    # Stammstrecke: 2 observations, mean 8.5, 1 row > 9 min (12.0).
    assert "| Beobachtungen (gesamt) | 2 |" in new_text
    assert "| Durchschnittliche Verspätung | 8.5 min |" in new_text
    assert "| Kritische Verspätungen (> 9 min) | 1 |" in new_text


def test_main_skips_readme_when_window_is_empty(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A run against an empty stats-dir must NOT overwrite the README.

    Defense-in-depth pin: the argparse default for ``--readme-path``
    is :data:`script.DEFAULT_README_PATH` (the production
    ``REPO_ROOT/README.md``), so any caller that supplies a stats
    directory but forgets to override the readme path can
    accidentally stamp ``_wird berechnet…_`` placeholder rows over
    real committed README content. Locked-in 2026-05-09 after
    ``test_main_*`` callers in
    :mod:`tests.scripts.test_generate_markdown_stats` did exactly
    that and contaminated the committed README (audit trail in PR
    #1397).

    Contract: when both windows are empty, the rendered placeholder
    block is NOT written to *readme_path* — the existing on-disk
    README content is preserved byte-for-byte.
    """
    empty_stats_dir = tmp_path / "missing"
    readme = tmp_path / "README.md"
    pre_image = "real README with markers — must survive an empty-window run"
    readme.write_text(
        _readme_with_markers(extra=f"\n{pre_image}\n"), encoding="utf-8"
    )
    snapshot = readme.read_bytes()
    output = tmp_path / "statistik.md"

    with caplog.at_level(logging.INFO, logger="generate_markdown_stats"):
        rc = script.main(
            [
                "--year",
                "2026",
                "--stats-dir",
                str(empty_stats_dir),
                "--output",
                str(output),
                "--readme-path",
                str(readme),
                "--now-iso",
                "2026-05-09T12:00:00+02:00",
            ]
        )
    assert rc == 0
    # README must be byte-identical — placeholder block did NOT land.
    assert readme.read_bytes() == snapshot
    # Dashboard still rendered (this branch only gates README writes).
    assert output.exists()
    # Audit log clearly explains the skip so operators can trace it.
    log_messages = " ".join(record.message for record in caplog.records)
    assert "README patch skipped entirely" in log_messages


def test_main_skip_readme_flag_leaves_readme_untouched(
    tmp_path: Path,
) -> None:
    stats_dir = tmp_path / "stats"
    _seed_csvs(stats_dir)
    readme = tmp_path / "README.md"
    readme.write_text(_readme_with_markers(), encoding="utf-8")
    snapshot = readme.read_bytes()
    output = tmp_path / "statistik.md"

    rc = script.main(
        [
            "--year",
            "2026",
            "--stats-dir",
            str(stats_dir),
            "--output",
            str(output),
            "--readme-path",
            str(readme),
            "--skip-readme",
            "--now-iso",
            "2026-05-09T12:00:00+02:00",
        ]
    )
    assert rc == 0
    assert readme.read_bytes() == snapshot
    # Dashboard still written.
    assert output.exists()


def test_main_skip_dashboard_flag_leaves_dashboard_untouched(
    tmp_path: Path,
) -> None:
    """``--skip-dashboard`` preserves docs/statistik.md byte-stable while
    still patching the README STATS markers.

    Mirrors the symmetric ``--skip-readme`` contract used by
    ``update-cycle.yml``: every 30-min cron tick patches the README
    snapshot, but only the cron tick that lands inside the 00:00
    Europe/Vienna hour additionally rewrites the yearly dashboard.
    """
    stats_dir = tmp_path / "stats"
    _seed_csvs(stats_dir)
    readme = tmp_path / "README.md"
    readme.write_text(_readme_with_markers(), encoding="utf-8")
    output = tmp_path / "statistik.md"
    pre_image = "# pre-existing dashboard content — must survive\n"
    output.write_text(pre_image, encoding="utf-8")
    output_snapshot = output.read_bytes()

    rc = script.main(
        [
            "--year",
            "2026",
            "--stats-dir",
            str(stats_dir),
            "--output",
            str(output),
            "--readme-path",
            str(readme),
            "--skip-dashboard",
            "--now-iso",
            "2026-05-09T12:00:00+02:00",
        ]
    )
    assert rc == 0
    # Dashboard file untouched (byte-stable).
    assert output.read_bytes() == output_snapshot
    # README markers patched with the real values.
    new_readme = readme.read_text(encoding="utf-8")
    assert "Beobachtungen (gesamt)" in new_readme
    assert "_Platzhalter Stammstrecke._" not in new_readme


def test_main_skip_dashboard_and_skip_readme_combined_is_noop(
    tmp_path: Path,
) -> None:
    """Combining ``--skip-dashboard`` with ``--skip-readme`` is a
    degenerate no-op: neither output is touched. The flags are
    independent toggles, not mutually exclusive — the script still
    exits 0 so an operator who passes both by accident gets a clean
    log instead of an error.
    """
    stats_dir = tmp_path / "stats"
    _seed_csvs(stats_dir)
    readme = tmp_path / "README.md"
    readme.write_text(_readme_with_markers(), encoding="utf-8")
    readme_snapshot = readme.read_bytes()
    output = tmp_path / "statistik.md"
    output.write_text("# untouched\n", encoding="utf-8")
    output_snapshot = output.read_bytes()

    rc = script.main(
        [
            "--year",
            "2026",
            "--stats-dir",
            str(stats_dir),
            "--output",
            str(output),
            "--readme-path",
            str(readme),
            "--skip-dashboard",
            "--skip-readme",
            "--now-iso",
            "2026-05-09T12:00:00+02:00",
        ]
    )
    assert rc == 0
    assert readme.read_bytes() == readme_snapshot
    assert output.read_bytes() == output_snapshot


def test_main_invalid_now_iso_returns_error(tmp_path: Path) -> None:
    stats_dir = tmp_path / "stats"
    _seed_csvs(stats_dir)
    readme = tmp_path / "README.md"
    readme.write_text(_readme_with_markers(), encoding="utf-8")
    output = tmp_path / "statistik.md"

    rc = script.main(
        [
            "--year",
            "2026",
            "--stats-dir",
            str(stats_dir),
            "--output",
            str(output),
            "--readme-path",
            str(readme),
            "--now-iso",
            "not-a-date",
        ]
    )
    assert rc == 1


def test_main_invalid_window_days_returns_error(tmp_path: Path) -> None:
    stats_dir = tmp_path / "stats"
    _seed_csvs(stats_dir)
    readme = tmp_path / "README.md"
    readme.write_text(_readme_with_markers(), encoding="utf-8")
    output = tmp_path / "statistik.md"

    rc = script.main(
        [
            "--year",
            "2026",
            "--stats-dir",
            str(stats_dir),
            "--output",
            str(output),
            "--readme-path",
            str(readme),
            "--readme-window-days",
            "0",
            "--now-iso",
            "2026-05-09T12:00:00+02:00",
        ]
    )
    assert rc == 1


def test_main_now_iso_with_offset_renders_named_timezone(
    tmp_path: Path,
) -> None:
    """``--now-iso`` carrying a numeric offset must still render the
    friendly TZ abbreviation in the README.

    Otherwise the README would silently flip from "CEST" / "CET" (named
    zone, what operators see in production where ``now`` is built from
    :func:`datetime.now(VIENNA_TZ)`) to ``UTC+02:00`` whenever someone
    reproduces a run via ``--now-iso "...+02:00"``. That asymmetry would
    confuse anyone trying to compare a local repro against the live
    workflow output.
    """
    stats_dir = tmp_path / "stats"
    _seed_csvs(stats_dir)
    readme = tmp_path / "README.md"
    readme.write_text(_readme_with_markers(), encoding="utf-8")
    output = tmp_path / "statistik.md"

    rc = script.main(
        [
            "--year",
            "2026",
            "--stats-dir",
            str(stats_dir),
            "--output",
            str(output),
            "--readme-path",
            str(readme),
            "--now-iso",
            "2026-05-09T12:00:00+02:00",  # offset, not ZoneInfo
        ]
    )
    assert rc == 0
    new_text = readme.read_text(encoding="utf-8")
    assert "| Letzte Aktualisierung | 2026-05-09 12:00 CEST |" in new_text
    assert "UTC+02:00" not in new_text


def test_main_now_iso_in_utc_is_converted_to_vienna_wall_clock(
    tmp_path: Path,
) -> None:
    """A UTC ``--now-iso`` must be converted to the equivalent Vienna
    wall clock so the timestamp string the operator sees in the README
    matches the ``Europe/Vienna`` semantics every other timestamp in
    the project uses."""
    stats_dir = tmp_path / "stats"
    _seed_csvs(stats_dir)
    readme = tmp_path / "README.md"
    readme.write_text(_readme_with_markers(), encoding="utf-8")
    output = tmp_path / "statistik.md"

    rc = script.main(
        [
            "--year",
            "2026",
            "--stats-dir",
            str(stats_dir),
            "--output",
            str(output),
            "--readme-path",
            str(readme),
            "--now-iso",
            "2026-05-09T10:00:00+00:00",  # 10:00 UTC == 12:00 Europe/Vienna in May
        ]
    )
    assert rc == 0
    new_text = readme.read_text(encoding="utf-8")
    assert "| Letzte Aktualisierung | 2026-05-09 12:00 CEST |" in new_text


def test_main_loads_previous_year_when_cutoff_crosses_january(
    tmp_path: Path,
) -> None:
    """30-day window in early January must include December rows.

    Otherwise the README Stammstrecke snapshot silently empties out
    every January, which would be a confusing UX bug for users opening
    the repo on New Year's Day.
    """
    stats_dir = tmp_path / "stats"
    stats_dir.mkdir(parents=True, exist_ok=True)
    (stats_dir / "stammstrecke_2025.csv").write_text(
        "timestamp,weekday,hour,direction,delay_minutes\n"
        "2025-12-20T08:00:00+01:00,Sa,8,Wien Hbf->Floridsdorf,7.0\n",
        encoding="utf-8",
    )
    (stats_dir / "stammstrecke_2026.csv").write_text(
        "timestamp,weekday,hour,direction,delay_minutes\n"
        "2026-01-02T08:00:00+01:00,Fr,8,Wien Hbf->Floridsdorf,3.0\n",
        encoding="utf-8",
    )
    readme = tmp_path / "README.md"
    readme.write_text(_readme_with_markers(), encoding="utf-8")
    output = tmp_path / "statistik.md"

    rc = script.main(
        [
            "--year",
            "2026",
            "--stats-dir",
            str(stats_dir),
            "--output",
            str(output),
            "--readme-path",
            str(readme),
            "--readme-window-days",
            "30",
            "--now-iso",
            "2026-01-05T12:00:00+01:00",
        ]
    )
    assert rc == 0
    new_text = readme.read_text(encoding="utf-8")
    # Both years' rows survive the 30-day filter.
    assert "| Beobachtungen (gesamt) | 2 |" in new_text
