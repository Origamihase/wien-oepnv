#!/usr/bin/env python3
"""Jahresrückblick der Baustellen, Probelauf: ask Gemini once and show what the guard says.

Owner's decision (Michael, 2026-11-10 17:44 Europe/Vienna), point 4: the
year's Baustellen are summarised by Gemini on the page of a finished year.
That page is written once a year, on 1 January, so nobody sees the prompt,
the model's answer and the guard at work in between. This script is the
dry run for people and for pull requests that touch the package: it reads
the year's Baustellen ledger, builds the prompt, asks Gemini once along
the usual chain, cleans and checks the answer exactly like the dashboard
run (``src/markdown_stats/year_review.py``) and prints a short report.

    python scripts/jahresrueckblick_probe.py --year 2026

It writes nothing into the repository and stores nothing: no review file,
no page. The report goes to stdout and, in a workflow, to the step summary.
The key comes from ``GEMINI_API_KEY``; without it the script says so and
asks nothing.

Exit code 0 unless the arguments are wrong: an overloaded Gemini, a
rejected text or a year without rows is a result to read, and must not
turn a pull request red.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from datetime import datetime
from logging import getLogger
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.feed.logging_safe import setup_script_logging
from src.ki_waechter.inputs import free_text
from src.markdown_stats.baustellen_year import load_sites
from src.markdown_stats.review_client import GEMINI_KEY_ENV, ReviewAnswer, ask_review
from src.markdown_stats.year_review import build_prompt, check_review, clean_review
from src.utils.stats import VIENNA_TZ
from src.utils.text import escape_markdown
from src.utils.vienna_clock import PLAUSIBLE_YEARS

LOGGER = getLogger("jahresrueckblick_probe")

Ask = Callable[[str], ReviewAnswer | None]


def _year(value: str) -> int:
    """``--year`` as a plausible calendar year (argparse reports anything else)."""
    try:
        year = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Jahr als Zahl angeben, zum Beispiel 2026") from None
    if not PLAUSIBLE_YEARS[0] <= year <= PLAUSIBLE_YEARS[1]:
        raise argparse.ArgumentTypeError(f"Jahr zwischen {PLAUSIBLE_YEARS[0]} und {PLAUSIBLE_YEARS[1]} angeben")
    return year


def _ask_from_env() -> Ask | None:
    """The real request with the environment's key; ``None`` without a key."""
    key = os.environ.get(GEMINI_KEY_ENV, "").strip()
    if not key:
        return None

    def ask(prompt: str) -> ReviewAnswer | None:
        return ask_review(prompt, key=key)

    return ask


def run_probe(year: int, *, stats_dir: Path | None = None, ask: Ask | None = None) -> str:
    """The probe's report for *year* as Markdown; *ask* defaults to the real request.

    Every text of the model and every reason of the guard is escaped for
    Markdown: the report lands in the step summary of a workflow run.
    """
    sites = load_sites(year, stats_dir=stats_dir)
    lines = [
        f"## Jahresrückblick Baustellen {year} – Probelauf",
        "",
        "Dieser Lauf speichert nichts und ändert keine Seite.",
        "",
        f"- Baustellen im Ledger: {len(sites)}",
    ]
    if not sites:
        return "\n".join([*lines, "- Urteil: nicht gefragt (das Jahr hat keine Baustellen im Ledger)", ""])
    prompt = build_prompt(sites, year=year)
    lines.append(f"- Eingabe an Gemini: {len(prompt)} Zeichen")
    asker = ask if ask is not None else _ask_from_env()
    if asker is None:
        return "\n".join([*lines, f"- Urteil: nicht gefragt ({GEMINI_KEY_ENV} ist leer)", ""])
    try:
        answer = asker(prompt)
    except Exception as exc:  # a probe reports, it does not fail
        LOGGER.warning("Probelauf: Anfrage gescheitert (%s).", type(exc).__name__)
        answer = None
    if answer is None:
        return "\n".join([*lines, "- Urteil: keine Antwort von Gemini (Einzelheiten im Protokoll des Laufs)", ""])
    text = clean_review(answer.text)
    reasons = check_review(text, sites, year=year)
    lines += [
        f"- Modell: {escape_markdown(free_text(answer.model)[:60] or '?')}",
        f"- Text: {len(text)} Zeichen",
        f"- Urteil: {'verworfen' if reasons else 'angenommen'}",
        "",
    ]
    if reasons:
        lines += ["Gründe der Prüfung:", "", *(f"- {escape_markdown(reason)}" for reason in reasons), ""]
    lines += ["Text des Modells:", "", f"> {escape_markdown(text) or '(leer)'}", ""]
    return "\n".join(lines)


def _write_summary(report: str) -> None:
    """Append *report* to the step summary of a workflow run; nothing outside one."""
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    try:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(report + "\n")
    except OSError as exc:
        # The summary is a convenience; stdout already carries the report.
        LOGGER.debug("Probelauf: Step-Summary nicht schreibbar (%s).", type(exc).__name__)


def main(argv: Sequence[str] | None = None, *, ask: Ask | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--year",
        type=_year,
        default=None,
        help="Jahr, dessen Baustellen zusammengefasst werden (Standard: das laufende Jahr, Wiener Zeit)",
    )
    parser.add_argument(
        "--stats-dir",
        type=Path,
        default=None,
        help="Ordner mit baustellen_<Jahr>.csv (Standard: data/stats)",
    )
    args = parser.parse_args(argv)
    setup_script_logging()
    year = args.year if args.year is not None else datetime.now(VIENNA_TZ).year
    report = run_probe(year, stats_dir=args.stats_dir, ask=ask)
    print(report)
    _write_summary(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
