"""The pages of finished years, ``docs/statistik-<Jahr>.md``.

``docs/statistik.md`` shows the running calendar year. It is rendered once
a day, shortly after midnight, so its last render of a year ran on 31.12.
at about 00:01, without New Year's Eve, and on 01.01. an almost empty new
year replaced it. The first dashboard run of a new year therefore also
writes the whole previous year, 31.12. included, to a page of its own
(Michael's choice "Jahresarchiv", 2026-10-03).

Review of 2026-11-10, before the first such run: the dashboard leads to the
archive pages and each archive page leads back (nobody found a page no
other page named), an archive page carries the coverage note of its year
(:func:`render_finished_year_coverage_note`), and it follows its ledger
instead of being written once (:func:`write_year_archives`).

Michael on 2026-11-10: the page of a year is created on 01.01., the year
can be chosen on the website under "Aktuelle Meldungen", the page sums up
all cancellations and delays in a statistic, and the roadworks of the year
are summarised by Gemini. So the page of a finished year holds two
sections the running dashboard does not have: the overview of the year
(:mod:`src.markdown_stats.year_stats`) and the roadworks with consequences
for public transport (:mod:`src.markdown_stats.baustellen_year`), whose
review in words Gemini writes (:mod:`src.markdown_stats.year_review`). That
review is his exception to "an AI only watches and reports, never
rewrites" (2026-10-31), which stays the rule for the feed.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from src.markdown_stats.baustellen_year import load_sites, render_baustellen_section
from src.markdown_stats.direction_coverage import (
    render_finished_year_coverage_note,
)
from src.markdown_stats.markdown_report import render_markdown, write_dashboard
from src.markdown_stats.rows import (
    LOGGER,
    aggregate_ausfaelle,
    aggregate_stammstrecke,
    aggregate_stoerungen,
    collect_year_data,
)
from src.markdown_stats.year_review import ensure_review, load_review
from src.markdown_stats.year_stats import render_year_overview
from src.utils.files import read_capped_text
from src.utils.liquid import liquid_safe
from src.utils.logging import sanitize_log_arg
from src.utils.stats import DEFAULT_STATS_DIR, VIENNA_TZ

# Days of a new year in which a missing review of the roadworks is only a
# log line. Gemini's free tier is overloaded for hours on some days (503 on
# 2026-10-31 and every morning from 01.11. to 05.11.2026); from 04.01. a
# year with roadworks and without a review fails the statistics step, which
# turns the health check red (Michael, 2026-10-28: "ein Fehler sollte
# auffallen").
REVIEW_GRACE_DAYS = 3


def archive_path(output_path: Path, year: int) -> Path:
    """``statistik-<year>.md`` next to the dashboard at *output_path*."""
    return output_path.with_name(f"{output_path.stem}-{year:04d}{output_path.suffix}")


def archived_years(output_path: Path, *, before: int) -> list[int]:
    """Years before *before* with an archive page next to *output_path*, newest first."""
    name = re.compile(
        rf"{re.escape(output_path.stem)}-(\d{{4}}){re.escape(output_path.suffix)}"
    )
    try:
        entries = [entry.name for entry in output_path.parent.iterdir()]
    except OSError:
        return []
    years = {int(m.group(1)) for m in map(name.fullmatch, entries) if m}
    return sorted((y for y in years if y < before), reverse=True)


def dashboard_nav(output_path: Path, *, year: int) -> str:
    """The dashboard's line to the archive pages; empty while there is none."""
    years = archived_years(output_path, before=year)
    if not years:
        return ""
    links = " · ".join(f"[{y}]({archive_path(output_path, y).name})" for y in years)
    return f"Frühere Jahre: {links}"


def archive_nav(output_path: Path) -> str:
    """An archive page's line back to the dashboard of the running year.

    The year switch of the website leads here (``docs/site.html``, section
    "Aktuelle Meldungen"), so the line also leads back there.
    """
    return (
        f"Abgeschlossenes Jahr · [Zur laufenden Statistik]({output_path.name})"
        " · [Zur Website](site.html)"
    )


def _baustellen_lines(
    year: int, *, now: datetime, stats_dir: Path | None, ask: bool
) -> list[str]:
    """The roadworks section of *year*; empty for a year without a row.

    With *ask* a year that has no accepted review stored is put to Gemini
    once (:func:`src.markdown_stats.year_review.ensure_review`); without it
    only a stored review is read. A year whose review is still missing says
    so in one line, whoever renders the page: a run without the key must
    not write another page than the run with it.
    """
    sites = load_sites(year, stats_dir=stats_dir)
    if not sites:
        return []
    if ask:
        review = ensure_review(year, sites, now=now, stats_dir=stats_dir).text
    else:
        review = load_review(year, sites, stats_dir=stats_dir)
    return render_baustellen_section(
        sites, year=year, review=review, pending=review is None
    )


def render_year_archive(
    *,
    now: datetime,
    year: int,
    stats_dir: Path | None,
    output_path: Path,
    ask: bool = False,
) -> str | None:
    """The archive page of the finished *year*; ``None`` when it has no rows.

    "Rows" are those of the three ledgers the dashboard shows; a year that
    only has roadworks gets no page. *ask* allows the one request to Gemini
    for a missing review of the roadworks (:func:`_baustellen_lines`); the
    dashboard run passes it, a reader of the page does not.
    """
    sm_rows, st_rows, au_rows = collect_year_data(year, stats_dir=stats_dir)
    if not (sm_rows or st_rows or au_rows):
        return None
    return render_markdown(
        year=year,
        generated_at=now,
        stammstrecke=aggregate_stammstrecke(sm_rows),
        stoerungen=aggregate_stoerungen(st_rows),
        ausfaelle=aggregate_ausfaelle(au_rows),
        coverage_note=render_finished_year_coverage_note(sm_rows, year=year),
        nav=archive_nav(output_path),
        after_summary=render_year_overview(sm_rows, st_rows, au_rows, year=year),
        before_sources=_baustellen_lines(
            year, now=now, stats_dir=stats_dir, ask=ask
        ),
    )


_LEDGER_NAME = re.compile(
    r"(?:stammstrecke|stoerungen|ausfaelle|baustellen)_(\d{4})\.csv"
)
_STAMP_PREFIX = "_Automatisch erzeugt am "


def finished_years(stats_dir: Path | None, *, before: int) -> list[int]:
    """Years before *before* that have a ledger file, oldest first."""
    base = stats_dir if stats_dir is not None else DEFAULT_STATS_DIR
    try:
        entries = [entry.name for entry in base.iterdir()]
    except OSError:
        return []
    years = {int(m.group(1)) for m in map(_LEDGER_NAME.fullmatch, entries) if m}
    return sorted(y for y in years if y < before)


def _without_stamp(page: str) -> list[str]:
    """The lines of *page* without the one that says when it was written."""
    return [line for line in page.splitlines() if not line.startswith(_STAMP_PREFIX)]


def write_year_archives(
    *,
    now: datetime,
    year: int,
    stats_dir: Path | None,
    output_path: Path,
) -> list[Path]:
    """Keep the page of every finished year in step with its ledger.

    Each dashboard run renders every year before *year* that has a ledger
    file and writes ``statistik-<Jahr>.md`` next to *output_path* when the
    page is missing or would read differently; the line with the time of
    writing does not count, so a page whose year did not change is not
    touched. A missed or late 01.01. tick does not matter: the next
    dashboard run writes the page.

    Until 2026-11-10 the page was written once and never again. The ledger
    of a year can still change after its first dashboard run: a train of
    31.12. is entered by the tick after its departure, and a tick that
    could not poll enters it later; rows are corrected and added by hand
    (26.10.2026: nine twin rows removed, 28.10.2026: 1,060 rows added to 33
    past days). The page then showed figures its own ledger no longer
    had, for good (Michael's decision of 2026-11-10: the archive follows
    the ledger).

    Nothing is written for an explicit ``--year`` other than the current
    one, or for a year without rows. Returns the paths written.
    """
    if year != now.year:
        return []
    written: list[Path] = []
    for finished in finished_years(stats_dir, before=year):
        markdown = render_year_archive(
            now=now,
            year=finished,
            stats_dir=stats_dir,
            output_path=output_path,
            ask=True,
        )
        if markdown is None:
            continue
        archive = archive_path(output_path, finished)
        if archive.is_file():
            current = read_capped_text(
                archive, label="Jahresarchiv", logger=LOGGER
            )
            if current is not None and _without_stamp(current) == _without_stamp(
                liquid_safe(markdown)
            ):
                continue
        write_dashboard(markdown, output_path=archive)
        LOGGER.info("Jahresarchiv geschrieben: %s.", sanitize_log_arg(str(archive)))
        written.append(archive)
    return written


def years_without_review(*, now: datetime, stats_dir: Path | None) -> list[int]:
    """Finished years with roadworks whose review is overdue, oldest first.

    A review is overdue when the year has roadworks in its ledger, no
    accepted review is stored and :data:`REVIEW_GRACE_DAYS` of the new year
    have passed. Every run of the statistics script asks this, not only the
    dashboard run after midnight: the health check shows the last run of
    the step, and the next run half an hour later would otherwise turn it
    green again while the review is still missing.
    """
    overdue: list[int] = []
    for finished in finished_years(stats_dir, before=now.year):
        opened = datetime(finished + 1, 1, 1 + REVIEW_GRACE_DAYS, tzinfo=VIENNA_TZ)
        if now < opened:
            continue
        sites = load_sites(finished, stats_dir=stats_dir)
        if sites and load_review(finished, sites, stats_dir=stats_dir) is None:
            overdue.append(finished)
    return overdue
