#!/usr/bin/env python3
"""Cross-reference ``pendler_candidates.json`` against ``stations.json``.

Thin CLI wrapper around :mod:`src.utils.pendler_audit`. Produces a
Markdown coverage report listing adopted candidates, orphans and
stale orphans grouped by priority. Useful as a CI gate for the curated
commuter whitelist and as an editor's checklist.

Run locally::

    python -m src.cli stations pendler-audit --output docs/pendler_candidates_audit.md

Exit codes:
    0  — report rendered successfully (default)
    1  — ``--fail-on-orphan`` was passed AND the audit found orphans
    2  — argparse rejected an invalid argument
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.utils.pendler_audit import (  # noqa: E402
    audit_pendler_candidates,
    load_candidates,
    load_pendler_station_keys,
    render_markdown,
)

DEFAULT_CANDIDATES_PATH = _ROOT / "data" / "pendler_candidates.json"
DEFAULT_STATIONS_PATH = _ROOT / "data" / "stations.json"
DEFAULT_MAX_STALE_DAYS = 365


def _parse_iso_date(value: str) -> date:
    """argparse ``type=`` callback that rejects malformed ISO dates."""
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid ISO date: {value!r} (expected YYYY-MM-DD)"
        ) from exc


def _build_parser() -> argparse.ArgumentParser:
    """Construct the script's argparse parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidates",
        type=Path,
        default=DEFAULT_CANDIDATES_PATH,
        help=f"Path to pendler_candidates.json (default: {DEFAULT_CANDIDATES_PATH})",
    )
    parser.add_argument(
        "--stations",
        type=Path,
        default=DEFAULT_STATIONS_PATH,
        help=f"Path to stations.json (default: {DEFAULT_STATIONS_PATH})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Optional file path for the Markdown report. "
            "Without --output the report is written to stdout."
        ),
    )
    parser.add_argument(
        "--max-stale-days",
        type=int,
        default=DEFAULT_MAX_STALE_DAYS,
        help=(
            "Days threshold above which an orphan is flagged stale "
            f"(default: {DEFAULT_MAX_STALE_DAYS}). "
            "Capped internally to MAX_STALE_DAYS_CAP via Sentinel min()."
        ),
    )
    parser.add_argument(
        "--reference-date",
        type=_parse_iso_date,
        default=None,
        help=(
            "Reference date for age and staleness computation "
            "(YYYY-MM-DD). Defaults to today's UTC date."
        ),
    )
    parser.add_argument(
        "--fail-on-orphan",
        action="store_true",
        help="Exit with status 1 when at least one orphan candidate is detected.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the audit; return the script's exit code.

    Args:
        argv: Optional argument list (mainly used by tests).

    Returns:
        Exit code (see module docstring).
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    reference_date: date = args.reference_date or date.today()  # noqa: DTZ011 - date.today is intentional here

    candidates = load_candidates(args.candidates)
    station_index = load_pendler_station_keys(args.stations)
    report = audit_pendler_candidates(
        candidates,
        station_index,
        reference_date=reference_date,
        max_stale_days=args.max_stale_days,
    )
    markdown = render_markdown(report, reference_date=reference_date)

    if args.output is not None:
        output_path: Path = args.output
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(markdown, encoding="utf-8")
        print(f"Report written to {output_path}")
    else:
        sys.stdout.write(markdown)
        if not markdown.endswith("\n"):
            sys.stdout.write("\n")

    if args.fail_on_orphan and report.has_orphans:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
