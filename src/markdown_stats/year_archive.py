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
rewrites" (2026-10-31), which stays the rule for the feed's texts.
"""
from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import NamedTuple, Protocol, TypeVar

from src.markdown_stats.baustellen_year import forget_reported, load_sites, render_baustellen_section
from src.markdown_stats.direction_coverage import (
    render_finished_year_coverage_note,
)
from src.markdown_stats.markdown_report import render_markdown, write_dashboard
from src.markdown_stats.review_client import DEFAULT_DEADLINE_S, GEMINI_KEY_ENV, MIN_REQUEST_S
from src.markdown_stats.rows import (
    LOGGER,
    AusfallRow,
    StammstreckeRow,
    StoerungRow,
    aggregate_ausfaelle,
    aggregate_stammstrecke,
    forget_header_warnings,
    aggregate_stoerungen,
    collect_year_data,
)
from src.markdown_stats.year_review import ensure_review, load_review, reset_dropped_review_log
from src.markdown_stats.year_stats import render_year_overview
from src.utils.files import read_capped_text
from src.utils.liquid import liquid_safe
from src.utils.logging import sanitize_log_arg
from src.utils.stats import DEFAULT_STATS_DIR, VIENNA_TZ, to_vienna

# Days of a new year in which a missing review of the roadworks is only a
# log line. Gemini's free tier is overloaded for hours on some days (503 on
# 2026-10-31 and every morning from 01.11. to 05.11.2026); from 04.01. a
# year with roadworks and without a review fails the statistics step of the
# Update cycle, which turns the health check red (Michael, 2026-10-28: "ein
# Fehler sollte auffallen"). Only that step asks for the failing exit code
# (``--fail-on-missing-review``, review of 2026-11-10).
REVIEW_GRACE_DAYS = 3

# The sources a page with the roadworks section names besides the three
# ledgers of the dashboard (review of 2026-11-10: the footer named only those).
BAUSTELLEN_SOURCE = "data/stats/baustellen_*.csv"
REVIEW_SOURCE = "data/stats/baustellen_rueckblick_*.json"

# Lines of this module already logged in the running statistics run. A
# dashboard run renders a page twice while its review is missing (first
# without requests, then with them), and until 2026-11-11 the same WARNING
# about rows of another year, and the same ERROR of a page that could not be
# written, stood twice in one log.
_LOGGED: set[tuple[str, ...]] = set()


def start_run() -> None:
    """Begin a statistics run: each finding is logged once in it, and again in the next run.

    It also resets the same memory of the roadworks ledger
    (:func:`~src.markdown_stats.baustellen_year.forget_reported`), of a wrong
    ledger header (:func:`~src.markdown_stats.rows.forget_header_warnings`) and of a
    dropped stored review
    (:func:`~src.markdown_stats.year_review.reset_dropped_review_log`): a
    process that runs the script twice (the tests) gets each run's lines.
    """
    _LOGGED.clear()
    forget_reported()
    forget_header_warnings()
    reset_dropped_review_log()


# The random part of the name ``atomic_write`` gives its temporary file
# (``statistik-2026.md.<32 hex>.tmp``): the same failed write of a page in
# both passes of a run names two such files.
_TEMP_NAME_RE = re.compile(r"\.[0-9a-f]{32}\.tmp\b")


def _log_once(level: int, message: str, *args: object) -> None:
    """Log *message* with *args* once per run.

    The key is the message with its arguments, the random part of a
    temporary file name left out.
    """
    key = (message, *(_TEMP_NAME_RE.sub(".tmp", str(arg)) for arg in args))
    if key in _LOGGED:
        return
    _LOGGED.add(key)
    LOGGER.log(level, message, *args)


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


class ReviewBudget:
    """One time budget for the review requests of all years of a run.

    Until the review of 2026-11-10 every finished year without a review got
    the client's whole deadline: two such years (a stored review of an
    older year stops passing a stricter guard while the new year is due)
    took about four minutes, the step's limit. Now the clock starts at the
    first request of the run and each later year gets what is left of
    :data:`~src.markdown_stats.review_client.DEFAULT_DEADLINE_S`, so all
    requests of a run end after about two minutes (the last one may start
    with :data:`~src.markdown_stats.review_client.MIN_REQUEST_S` left, plus
    its connect timeout). The read timeout bounds each wait for data, not
    the whole answer, so a server that keeps sending slowly can hold the
    last request longer; the step's limit of four minutes in
    ``update-cycle.yml`` is the hard bound. *clock* is for tests.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._started: float | None = None

    def left(self) -> float:
        """Seconds left for requests; the first call starts the clock."""
        now = self._clock()
        if self._started is None:
            self._started = now
        return DEFAULT_DEADLINE_S - (now - self._started)


class _Section(NamedTuple):
    """The roadworks section of a page and the files it was made from."""

    lines: list[str]
    sources: tuple[str, ...]


def _baustellen_section(
    year: int, *, now: datetime, stats_dir: Path | None, budget: ReviewBudget | None
) -> _Section:
    """The roadworks section of *year*; empty for a year without a row.

    Without *budget* only a stored review is read. With it, a year whose
    stored review is missing or no longer passes the guard is put to Gemini
    once (:func:`src.markdown_stats.year_review.ensure_review`) with what is
    left of the run's budget. A year whose review is still missing says so
    in one line, whoever renders the page: a run without the key must not
    write another page than the run with it.
    """
    sites = load_sites(year, stats_dir=stats_dir)
    if not sites:
        return _Section([], ())
    review = load_review(year, sites, stats_dir=stats_dir)
    if review is None and budget is not None:
        left = budget.left()
        if left < MIN_REQUEST_S:
            LOGGER.warning(
                "Baustellen-Rückblick %d: in diesem Lauf keine Anfrage mehr, die Zeit "
                "für Anfragen ist aufgebraucht; ein späterer Dashboard-Lauf fragt erneut.",
                year,
            )
        else:
            review = ensure_review(
                year, sites, now=now, stats_dir=stats_dir, deadline_s=left
            ).text
    lines = render_baustellen_section(
        sites, year=year, review=review, pending=review is None
    )
    sources = (BAUSTELLEN_SOURCE,) if review is None else (BAUSTELLEN_SOURCE, REVIEW_SOURCE)
    return _Section(lines, sources)


class _Stamped(Protocol):
    @property
    def timestamp(self) -> datetime:
        """The instant of the row; :func:`_of_year` takes its Vienna year."""


_RowT = TypeVar("_RowT", bound=_Stamped)


def _of_year(rows: Sequence[_RowT], year: int) -> list[_RowT]:
    return [row for row in rows if to_vienna(row.timestamp).year == year]


def _rows_of_year(
    year: int, *, stats_dir: Path | None
) -> tuple[list[StammstreckeRow], list[StoerungRow], list[AusfallRow], int]:
    """The rows of the three dashboard ledgers of *year* that lie in its Vienna year.

    The fourth value counts the rows left out. The writers file every row
    under its Vienna year, but a row added or corrected by hand can carry
    another year. Until the review of 2026-11-10 the key figures and the
    old sections counted such a row under the year while the overview left
    it out, so one page gave two totals; a file with only such rows got a
    page without an overview.
    """
    sm_all, st_all, au_all = collect_year_data(year, stats_dir=stats_dir)
    sm_rows, st_rows, au_rows = _of_year(sm_all, year), _of_year(st_all, year), _of_year(au_all, year)
    left_out = len(sm_all) + len(st_all) + len(au_all) - len(sm_rows) - len(st_rows) - len(au_rows)
    return sm_rows, st_rows, au_rows, left_out


def render_year_archive(
    *,
    now: datetime,
    year: int,
    stats_dir: Path | None,
    output_path: Path,
    budget: ReviewBudget | None = None,
) -> str | None:
    """The archive page of the finished *year*; ``None`` when it has no rows.

    "Rows" are those of the three ledgers the dashboard shows whose Vienna
    year is *year*; a year that only has roadworks gets no page. A row of
    another year is left out of every figure and named in one log line,
    once per run (:func:`start_run`).
    *budget* allows the requests to Gemini for a missing review of the
    roadworks (:func:`_baustellen_section`); without it only a stored
    review is read.

    The coverage note of the year stands once, at the head of the overview
    (review of 2026-11-10): its words "Die Jahreszahlen unten" are then true
    for every figure of the page; under "## Stammstrecke", where it stood
    before, the overview above it set Meidling (recorded to 10.11.2026)
    beside Praterstern (to 14.08.2026) with no word why. Only a page without
    an overview keeps it under "Beobachtungen je Richtung".
    """
    sm_rows, st_rows, au_rows, left_out = _rows_of_year(year, stats_dir=stats_dir)
    if left_out:
        _log_once(
            logging.WARNING,
            "Jahresarchiv %d: %d %s mit dem Zeitstempel eines anderen Jahres nicht gezählt.",
            year,
            left_out,
            "Zeile" if left_out == 1 else "Zeilen",
        )
    if not (sm_rows or st_rows or au_rows):
        return None
    note = render_finished_year_coverage_note(sm_rows, year=year)
    overview = render_year_overview(sm_rows, st_rows, au_rows, year=year, coverage_note=note)
    baustellen = _baustellen_section(year, now=now, stats_dir=stats_dir, budget=budget)
    return render_markdown(
        year=year,
        generated_at=now,
        stammstrecke=aggregate_stammstrecke(sm_rows),
        stoerungen=aggregate_stoerungen(st_rows),
        ausfaelle=aggregate_ausfaelle(au_rows),
        coverage_note="" if overview else note,
        nav=archive_nav(output_path),
        after_summary=overview,
        before_sources=baustellen.lines,
        extra_sources=baustellen.sources,
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
    ask: bool = False,
    clock: Callable[[], float] = time.monotonic,
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

    Without *ask* only stored reviews are read; the statistics script
    writes the pages so first and everything else of the run before any
    request (review of 2026-11-10: a wait for Gemini or a failure in the
    pages had cost the dashboard, the summary and the README of the run).
    With *ask* the years are taken newest first under one
    :class:`ReviewBudget` (*clock*), so the year just finished is asked
    before an older one whose review stopped passing.

    Nothing is written for an explicit ``--year`` other than the current
    one, or for a year without rows. Returns the paths written.
    """
    if year != now.year:
        return []
    budget = ReviewBudget(clock) if ask else None
    written: list[Path] = []
    for finished in reversed(finished_years(stats_dir, before=year)):
        markdown = render_year_archive(
            now=now,
            year=finished,
            stats_dir=stats_dir,
            output_path=output_path,
            budget=budget,
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


def _review_missing(year: int, *, stats_dir: Path | None) -> bool:
    """Whether *year* has a page and roadworks but no accepted review stored.

    The year's rows are read last, only while a review is missing: a year
    without a row in the three ledgers of the dashboard gets no page
    (:func:`render_year_archive`), so no run asks for its review and it
    cannot be overdue. Until the review of 2026-11-10 such a year (only a
    Baustellen ledger) failed the step on every tick, for good.
    """
    sites = load_sites(year, stats_dir=stats_dir)
    if not sites or load_review(year, sites, stats_dir=stats_dir) is not None:
        return False
    sm_rows, st_rows, au_rows, _ = _rows_of_year(year, stats_dir=stats_dir)
    return bool(sm_rows or st_rows or au_rows)


def years_missing_review(*, before: int, stats_dir: Path | None) -> list[int]:
    """Finished years before *before* that a dashboard run asks Gemini for, oldest first.

    A year with a page and roadworks and no accepted review stored. The
    statistics script sends requests only while this list is not empty.
    """
    return [
        finished
        for finished in finished_years(stats_dir, before=before)
        if _review_missing(finished, stats_dir=stats_dir)
    ]


def years_without_review(*, now: datetime, stats_dir: Path | None) -> list[int]:
    """Finished years with roadworks whose review is overdue, oldest first.

    A review is overdue when the year has a page and roadworks, no accepted
    review is stored and :data:`REVIEW_GRACE_DAYS` of the new year have
    passed. Every run of the Update cycle's statistics step asks this
    (``--fail-on-missing-review``), not only the dashboard run after
    midnight: the health check shows the last run of the step, and the
    next run half an hour later would otherwise turn it green again while
    the review is still missing. Within the grace days nothing is read.
    """
    return [
        finished
        for finished in finished_years(stats_dir, before=now.year)
        if now >= datetime(finished + 1, 1, 1 + REVIEW_GRACE_DAYS, tzinfo=VIENNA_TZ)
        and _review_missing(finished, stats_dir=stats_dir)
    ]


def write_year_pages(
    *, now: datetime, year: int, stats_dir: Path | None, output_path: Path, ask: bool
) -> bool:
    """The statistics script's call of :func:`write_year_archives`; ``False`` when it failed.

    The dashboard run calls it twice (review of 2026-11-10): first without
    *ask*, before the summary, the dashboard and the README, which name the
    pages; last with *ask*, after all of them, and only while
    :func:`years_missing_review` names a year, so nothing of the run waits
    for Gemini and a kill by the step's time limit costs only the review.
    Any exception is one German ERROR line with its class: until that
    review only an ``OSError`` was caught, a defect in the code of the page
    left the run as a traceback, and a failed page ended it before the
    summary, the dashboard and the README. The line stands once per run
    (:func:`start_run`): the same failure in the second call is not logged
    again, another one is.
    """
    try:
        if ask and not years_missing_review(before=year, stats_dir=stats_dir):
            return True
        write_year_archives(now=now, year=year, stats_dir=stats_dir, output_path=output_path, ask=ask)
    except Exception as exc:  # the other outputs of the run must not depend on it
        _log_once(
            logging.ERROR,
            "Konnte Jahresarchiv nicht schreiben (%s): %s",
            type(exc).__name__,
            sanitize_log_arg(str(exc)),
        )
        return False
    return True


def no_request_note(*, year: int, stats_dir: Path | None) -> str:
    """The end of the ERROR line of a failed write in the dashboard run of *year*.

    Such a failure ends the run before its requests: what the website and
    the README show is the bigger alarm. While a finished year waits for
    its review (:func:`years_missing_review`) the line says that this run
    sends none; otherwise it is empty. The check only reads; should it
    fail, the line stays as it was. The note starts with a dash: it follows
    the error text of the failed write, which ends without a full stop
    (until the review of 2026-11-11 the two ran together).
    """
    try:
        years = years_missing_review(before=year, stats_dir=stats_dir)
    except Exception:  # the failed write is the message; this only adds to it
        return ""
    if not years:
        return ""
    reviews = "den fehlenden Rückblick" if len(years) == 1 else "die fehlenden Rückblicke"
    return (
        f" — der Lauf endet hier, ohne Anfrage für {reviews} der Baustellen "
        + ", ".join(str(missing) for missing in years)
        + "; ein späterer Dashboard-Lauf fragt erneut."
    )


def overdue_reviews_reported(*, now: datetime, stats_dir: Path | None) -> bool:
    """Whether a review is overdue (:func:`years_without_review`); if so, one ERROR line.

    The line says what the check knows: no stored text passes the guard.
    The cause is in the log of the dashboard run (lines
    "Baustellen-Rückblick …"); until the review of 2026-11-10 the line named
    Gemini as the cause also when nothing was sent because the key was
    empty. An empty key of this run is said.
    """
    overdue = years_without_review(now=now, stats_dir=stats_dir)
    if not overdue:
        return False
    key_note = "" if os.environ.get(GEMINI_KEY_ENV, "").strip() else f" {GEMINI_KEY_ENV} ist in diesem Lauf leer."
    LOGGER.error(
        "Rückblick der Baustellen fehlt für %s: kein Text gespeichert, der die "
        "Prüfung besteht (Grund im Protokoll des Laufs kurz nach Mitternacht).%s",
        ", ".join(str(year) for year in overdue),
        key_note,
    )
    return True
