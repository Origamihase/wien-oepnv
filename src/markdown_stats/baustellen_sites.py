"""The year's Baustellen sites, read from the ledger and merged.

Holds the reading of ``data/stats/baustellen_<Jahr>.csv`` (one row per guid),
the rules that say which rows of one title are one site, and
:func:`load_sites`. Moved verbatim out of
``src/markdown_stats/baustellen_year.py``, which re-exports the names another file imports or a test reads; the
log lines keep the logger of ``src/markdown_stats/rows.py``.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Final

from src.markdown_stats.rows import LOGGER, MAX_CSV_BYTES
from src.utils.files import read_capped_text
from src.utils.logging import sanitize_log_arg
from src.utils.stats import stats_path
from src.utils.stats_baustellen import BAUSTELLEN_HEADER, SiteLine, read_ledger_text
from src.utils.vienna_clock import PLAUSIBLE_YEARS

__all__ = [
    "LEDGER_KIND", "MAX_DESCRIPTIONS", "BaustelleSite", "forget_reported", "load_sites",
]

LEDGER_KIND: Final = "baustellen"
# Distinct description texts kept per site, newest first. A prolonged site
# repeats its text under every new guid; three keep the changes of a year
# without carrying one text many times.
MAX_DESCRIPTIONS: Final = 3


@dataclass(frozen=True)
class BaustelleSite:
    """One site of the year: the ledger rows of one title and one stretch of time, merged."""

    title: str
    starts: date | None
    ends: date | None
    district: str
    measure: str
    descriptions: tuple[str, ...]
    first_seen: datetime


@dataclass(frozen=True)
class _LedgerRow:
    """One parsed ledger row; ``seen`` is its time stamp in Vienna time."""

    seen: datetime
    guid: str
    title: str
    starts: date | None
    ends: date | None
    district: str
    measure: str
    description: str


# ---- reading the ledger -----------------------------------------------------


def _one_line(value: str) -> str:
    """*value* with every whitespace run as one space."""
    return " ".join(value.split())


def _ledger_date(value: str) -> date | None:
    """``YYYY-MM-DD`` as a date; empty, unreadable or implausible is not set.

    A year outside :data:`src.utils.vienna_clock.PLAUSIBLE_YEARS` is how a
    source writes "no end" (owner's decision of 2026-11-10); the page then
    shows the end as open instead of "31.12.9999".
    """
    try:
        parsed = date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None
    return parsed if PLAUSIBLE_YEARS[0] <= parsed.year <= PLAUSIBLE_YEARS[1] else None


def _parse_row(site: SiteLine) -> _LedgerRow:
    """The fields of a ledger line that :func:`site_line` took for a site."""
    row = dict(zip(BAUSTELLEN_HEADER, site.cells, strict=True))
    return _LedgerRow(
        seen=site.seen,
        guid=row["guid"].strip(),
        title=_one_line(row["title"]),
        starts=_ledger_date(row["starts_at"]),
        ends=_ledger_date(row["ends_at"]),
        district=_one_line(row["district"]),
        measure=_one_line(row["measure"]),
        description=_one_line(row["description"]),
    )


# Log lines already written in this process, so a statistics run that reads
# one ledger several times (the page, the request pass, the check for an
# overdue review) says each finding once.
_REPORTED: set[tuple[str, ...]] = set()


def _report_once(level: int, message: str, path: Path, *args: int) -> None:
    """Log *message* about *path* with *args* once per process.

    The key is the message with its arguments, so the same file with
    another count, or another year's file, is logged again.
    """
    key = (message, str(path), *map(str, args))
    if key in _REPORTED:
        return
    _REPORTED.add(key)
    LOGGER.log(level, message, sanitize_log_arg(str(path)), *args)


def forget_reported() -> None:
    """Let the next statistics run in this process log its findings again."""
    _REPORTED.clear()


def _read_rows(path: Path) -> Iterator[_LedgerRow]:
    """Every readable row of the ledger at *path*.

    The file is read through the size cap of the other ledgers and taken
    apart line by line (:func:`src.utils.stats_baustellen.read_ledger_text`,
    the same function the ledger's writer uses, so a line counts for both
    or for neither): a ledger row is one physical line (the writer
    collapses line breaks, and ``data/stats/*.csv merge=union`` in
    ``.gitattributes`` merges by line), so one damaged line, an unbalanced
    quote for instance, costs that line and not the rest of the file. A
    line that repeats the header (two branches created the file) is
    skipped, a file that starts with another header is not read at all; a
    byte order mark and blank lines before the header are passed over.

    Bytes that are no UTF-8 are replaced, as the writer does. A line that
    still reads as a whole record is kept, the replacement character shown
    like any other: the city's text can hold a real U+FFFD, and a site
    dropped for it was never entered again by the writer (review of
    2026-11-10). A line that does not read is skipped and counted.
    """
    raw = read_capped_text(path, max_bytes=MAX_CSV_BYTES, errors="replace", label="stats CSV", logger=LOGGER)
    if raw is None or not raw.strip():
        return
    ledger = read_ledger_text(raw)
    if ledger is None:
        _report_once(logging.WARNING, "Stats-Datei %s hat unerwarteten Header — überspringe.", path)
        return
    yield from (_parse_row(site) for site in ledger.sites)
    if ledger.unreadable:
        _report_once(logging.WARNING, "Stats-Datei %s: %d unlesbare Zeile(n) übersprungen.", path, ledger.unreadable)


def _first_per_guid(rows: Iterator[_LedgerRow]) -> list[_LedgerRow]:
    """One row per guid, the earliest: the ledger's row is the first sighting.

    A line that stands twice (a union merge keeps both sides) and a guid
    written again by hand count once.
    """
    first: dict[str, _LedgerRow] = {}
    for row in rows:
        known = first.get(row.guid)
        if known is None or row.seen < known.seen:
            first[row.guid] = row
    return list(first.values())


def _title_key(title: str) -> str:
    """What makes two rows the same site: the title, case and spacing aside.

    The cut mark at the end does not count either: the feed marks a title
    the city delivers cut off with "…" (``_mark_upstream_truncation``), and
    a row written before that rule carries the same title without it.
    """
    return _one_line(title).rstrip(" .…").casefold()


def _outside_year(row: _LedgerRow, year: int) -> bool:
    """Whether the row's period ended before *year* began or starts after it ends.

    The writer files a row under the year of the cache write, whatever the
    period: the city publishes a site some days before its start and still
    delivers it the day after its end, so a site of the next or the last
    year can stand in this year's ledger. It is no site of this year. A
    row without a start or an end is kept on that side: a missing date
    does not show that the site lay outside the year.
    """
    return (row.ends is not None and row.ends < date(year, 1, 1)) or (
        row.starts is not None and row.starts > date(year, 12, 31)
    )


def _periods_touch(one: _LedgerRow, other: _LedgerRow) -> bool:
    """Whether the periods of two rows overlap or touch (no free day between them).

    A missing start or end is open on that side, so it reaches every
    period on that side.
    """
    one_start, one_end = one.starts or date.min, one.ends or date.max
    other_start, other_end = other.starts or date.min, other.ends or date.max
    return (other_start - one_end).days <= 1 and (one_start - other_end).days <= 1


def _same_site(earlier: _LedgerRow, later: _LedgerRow) -> bool:
    """Whether *later* (first seen after *earlier*) belongs to the site of *earlier*.

    Yes when their periods overlap or touch, and when *later* was first
    seen on or before the last day of *earlier*'s period (an open end has
    no last day: it reaches every later row, also one wholly before its
    start): the city replaced the period while the site was announced.
    """
    if _periods_touch(earlier, later):
        return True
    return earlier.ends is None or later.seen.date() <= earlier.ends


def _period_row(rows: Sequence[_LedgerRow]) -> _LedgerRow:
    """The row whose period a site shows: the newest that names a start or an end.

    Without such a row, the newest row. Ties in the first sighting keep
    the order of *rows*. :func:`_merge` shows this period and
    :func:`_sites_of_title` compares a new row with it, so a period the
    city has replaced is neither shown nor matched.
    """
    newest_first = sorted(rows, key=lambda row: row.seen, reverse=True)
    return next(
        (row for row in newest_first if row.starts is not None or row.ends is not None),
        newest_first[0],
    )


def _sites_of_title(rows: Sequence[_LedgerRow]) -> list[list[_LedgerRow]]:
    """The rows of one title, grouped into sites.

    The rows are taken in the order of their first sighting. A row joins
    every site whose current period it belongs to (:func:`_same_site`
    against the row :func:`_period_row` picks), and sites it joins become
    one. Only a row first seen after the current period had ended, with a
    period that neither overlaps nor touches it, starts a site of its own:
    the same address worked on in March and again in October is two
    sites; merged, the list showed one site from March to November and
    every month between counted as touched.

    A site the city postpones past its old end is one site: the later
    period was entered before the old one ended, so the old one was
    withdrawn (split, it was listed and counted as a site of its own). A
    withdrawn period is not compared either: when the city shortens a
    site's end (5 of the 19 sites of 2026), a job under the same title
    after the new end is a site of its own, though it lies inside the
    withdrawn period (both: review of 2026-11-10). Each site shows its
    current period (:func:`_merge`). Limits: a current period with an
    open end, or without any date, reaches every later row of its title;
    a second job the city announces under the same title while the first
    is still under way counts as a new period of the first.
    """
    groups: list[list[_LedgerRow]] = []
    for row in sorted(rows, key=lambda row: (row.seen, row.guid)):
        joined = [group for group in groups if _same_site(_period_row(group), row)]
        groups = [group for group in groups if not any(group is other for other in joined)]
        groups.append([*(earlier for group in joined for earlier in group), row])
    return groups


def _merge(rows: Sequence[_LedgerRow]) -> BaustelleSite:
    """One site from rows of one title (see the module docstring).

    The period is the one of the newest row that names a start or an end,
    both dates from that row: the city also moves an end earlier, and the
    latest end any row ever named stood on the 2026 page for 5 of 19
    sites after the city had withdrawn it. An end that row leaves open is
    shown as open, not filled from an older row. Title, district and
    measure are the newest row's; an empty cell there falls back to the
    newest row that has one. The description texts are kept once each,
    newest first, at most :data:`MAX_DESCRIPTIONS`.
    """
    newest_first = sorted(rows, key=lambda row: row.seen, reverse=True)
    dated = _period_row(rows)
    descriptions: list[str] = []
    for row in newest_first:
        if row.description and row.description not in descriptions:
            descriptions.append(row.description)
    return BaustelleSite(
        title=newest_first[0].title,
        starts=dated.starts,
        ends=dated.ends,
        district=next((row.district for row in newest_first if row.district), ""),
        measure=next((row.measure for row in newest_first if row.measure), ""),
        descriptions=tuple(descriptions[:MAX_DESCRIPTIONS]),
        first_seen=min(row.seen for row in rows),
    )


def _site_order(site: BaustelleSite) -> tuple[bool, date, str, str]:
    """By start, sites without a start last, then by title."""
    return (site.starts is None, site.starts or date.min, site.title.casefold(), site.title)


def load_sites(year: int, *, stats_dir: Path | None = None) -> list[BaustelleSite]:
    """The sites of *year* from ``baustellen_<year>.csv``, in the page's order.

    Reads the ledger under *stats_dir* (default: the statistics folder,
    looked up at call time through :func:`src.utils.stats.stats_path`),
    keeps one row per guid, leaves out every row whose period lies wholly
    outside *year* (:func:`_outside_year`; counted in one log line) and
    merges the rows of one title into sites (:func:`_sites_of_title`).
    The row filter comes first, so a row of
    another year cannot lend its period to a site of this one. A missing,
    oversized or foreign file gives no sites; a damaged line is skipped
    and counted in one log line. Sorted by start (sites without one
    last), then by title.
    """
    path = stats_path(LEDGER_KIND, year, base_dir=stats_dir)
    by_title: dict[str, list[_LedgerRow]] = {}
    outside = 0
    for row in _first_per_guid(_read_rows(path)):
        if _outside_year(row, year):
            outside += 1
            continue
        by_title.setdefault(_title_key(row.title), []).append(row)
    if outside:
        _report_once(
            logging.INFO, "Stats-Datei %s: %d Zeile(n) mit Zeitraum außerhalb des Jahres %d nicht gezählt.",
            path, outside, year,
        )
    groups = (group for rows in by_title.values() for group in _sites_of_title(rows))
    return sorted((_merge(group) for group in groups), key=_site_order)
