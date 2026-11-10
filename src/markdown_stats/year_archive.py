"""The pages of finished years, ``docs/statistik-<Jahr>.md``.

``docs/statistik.md`` shows the running calendar year. It is rendered once
a day, shortly after midnight, so its last render of a year ran on 31.12.
at about 00:01, without New Year's Eve, and on 01.01. an almost empty new
year replaced it. The first dashboard run of a new year therefore also
writes the whole previous year, 31.12. included, to a page of its own
(Michael's choice "Jahresarchiv", 2026-10-03).

Review of 2026-11-10, before the first such run: the dashboard leads to the
archive pages and each archive page leads back (nobody found a page no
other page named), and an archive page carries the coverage note of its
year (:func:`render_finished_year_coverage_note`).
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
from src.utils.logging import sanitize_log_arg


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


def write_year_archive(
    *,
    now: datetime,
    year: int,
    stats_dir: Path | None,
    output_path: Path,
) -> Path | None:
    """Keep the finished previous year as ``statistik-<Vorjahr>.md`` next to *output_path*.

    Written by whichever dashboard run first finds the page missing, so a
    missed or late 01.01. tick does not matter. Nothing is written for an
    explicit ``--year`` other than the current one, or when the previous
    year has no rows at all. Returns the path written, if any.
    """
    if year != now.year:
        return None
    previous = year - 1
    archive = archive_path(output_path, previous)
    if archive.exists():
        return None
    markdown = render_year_archive(
        now=now, year=previous, stats_dir=stats_dir, output_path=output_path
    )
    if markdown is None:
        return None
    write_dashboard(markdown, output_path=archive)
    LOGGER.info("Jahresarchiv geschrieben: %s.", sanitize_log_arg(str(archive)))
    return archive
