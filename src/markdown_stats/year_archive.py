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
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

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
from src.utils.files import read_capped_text
from src.utils.liquid import liquid_safe
from src.utils.logging import sanitize_log_arg
from src.utils.stats import DEFAULT_STATS_DIR


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
    """An archive page's line back to the dashboard of the running year."""
    return f"Abgeschlossenes Jahr · [Zur laufenden Statistik]({output_path.name})"


def render_year_archive(
    *,
    now: datetime,
    year: int,
    stats_dir: Path | None,
    output_path: Path,
) -> str | None:
    """The archive page of the finished *year*; ``None`` when it has no rows."""
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
    )


_LEDGER_NAME = re.compile(r"(?:stammstrecke|stoerungen|ausfaelle)_(\d{4})\.csv")
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
            now=now, year=finished, stats_dir=stats_dir, output_path=output_path
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
