"""Command-line options of the statistics dashboard run.

The argument parser of ``scripts/generate_markdown_stats.py`` and the
default README path it offers. Moved verbatim from the script (line limit,
2026-11-13); :func:`src.markdown_stats.cli.main` builds the parser once per
run, the script re-exports ``DEFAULT_README_PATH``.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Final

from src.markdown_stats.markdown_report import DEFAULT_OUTPUT_PATH, REPO_ROOT
from src.markdown_stats.readme import DEFAULT_README_WINDOW_DAYS
from src.markdown_stats.summary import SUMMARY_FILENAME
from src.utils.stats import DEFAULT_STATS_DIR

__all__ = ["DEFAULT_README_PATH", "build_parser"]


DEFAULT_README_PATH: Final = REPO_ROOT / "README.md"


def build_parser() -> argparse.ArgumentParser:
    """The options of the run, with the defaults read when the run starts."""
    parser = argparse.ArgumentParser(
        description="Generate the Wien ÖPNV statistics dashboard.",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=None,
        help=(
            "Calendar year to aggregate (default: the Vienna year of the "
            "run time, i.e. of --now-iso when given)."
        ),
    )
    parser.add_argument(
        "--stats-dir",
        type=Path,
        default=DEFAULT_STATS_DIR,
        help=f"Directory containing the stats CSV files (default: {DEFAULT_STATS_DIR}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=f"Output Markdown path (default: {DEFAULT_OUTPUT_PATH}).",
    )
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=None,
        help=(
            "Output path for the pre-computed dashboard summary JSON "
            "(default: a sibling of --output named "
            f"{SUMMARY_FILENAME})."
        ),
    )
    parser.add_argument(
        "--readme-path",
        type=Path,
        default=DEFAULT_README_PATH,
        help=(
            "Path to the README.md whose <!-- STATS:* --> markers should be "
            f"patched (default: {DEFAULT_README_PATH})."
        ),
    )
    parser.add_argument(
        "--readme-window-days",
        type=int,
        default=DEFAULT_README_WINDOW_DAYS,
        help=(
            "Window size in days for the README snapshot block "
            f"(default: {DEFAULT_README_WINDOW_DAYS})."
        ),
    )
    parser.add_argument(
        "--skip-readme",
        action="store_true",
        help=(
            "Skip the README patch entirely (only the docs/statistik.md "
            "dashboard is regenerated)."
        ),
    )
    parser.add_argument(
        "--skip-dashboard",
        action="store_true",
        help=(
            "Skip regenerating docs/statistik.md (only the README STATS "
            "markers are patched). Inverse of ``--skip-readme``; used by "
            "``update-cycle.yml`` so the dashboard refreshes once daily "
            "at the 00:00 Europe/Vienna tick while the README snapshot "
            "continues to update on every 30-min tick."
        ),
    )
    parser.add_argument(
        "--now-iso",
        type=str,
        default=None,
        help=(
            "Override the wall clock used for the README window cutoff and "
            "the 'Letzte Aktualisierung' cell. Accepts an ISO 8601 string; "
            "missing tzinfo is interpreted as Europe/Vienna. Intended for "
            "deterministic reproductions and tests."
        ),
    )
    parser.add_argument(
        "--fail-on-missing-review",
        action="store_true",
        help=(
            "End with 1 while a finished year's review of the roadworks is "
            "overdue (from 04.01.); only update-cycle.yml passes it."
        ),
    )
    return parser
