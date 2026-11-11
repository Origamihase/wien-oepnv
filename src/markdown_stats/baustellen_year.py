"""The Baustellen section of a finished year's page, read from the ledger.

Owner's decision (Michael, 2026-11-10 17:44 Europe/Vienna), point 4: "The
year's Baustellen (roadworks) are summarised by Gemini." The summary in
words is the second half of that (``src/markdown_stats/year_review.py``);
this module is the first half and the fallback: the count and the list of
the year's sites come straight from ``data/stats/baustellen_<Jahr>.csv``
and stand on the page whether or not a review exists.

The ledger holds one row per guid of the Stadt Wien data. That guid is a
hash of title, start and end (``src/providers/baustellen_events.py``), so
a site whose end was moved appears under a new guid; the 2026 ledger
holds 61 rows for 19 sites. :func:`load_sites` therefore merges the rows
of one title into one site and shows the period the city named last
(review of 2026-11-10: the city also moves an end earlier, postpones a
site past its old end, and the same address can be worked on twice);
:func:`_sites_of_title` says when two rows of one title are two sites.

Licence: the texts are the city's, CC BY 4.0, and appear edited
(``NOTICE.md``, "Generierte Feeds"): the section ends with the source, the
note on the editing and the licence link.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Final

from src.markdown_stats.rows import LOGGER, MAX_CSV_BYTES
from src.utils.files import read_capped_text
from src.utils.logging import sanitize_log_arg
from src.utils.stats import stats_path, to_vienna
from src.utils.stats_baustellen import BAUSTELLEN_HEADER, SiteLine, read_ledger_text
from src.utils.text import escape_markdown_cell, normalise_markdown_text
from src.utils.vienna_clock import PLAUSIBLE_YEARS

LEDGER_KIND: Final = "baustellen"
SECTION_HEADING: Final = "## Baustellen mit Folgen für den öffentlichen Verkehr"
REVIEW_HEADING: Final = "### Rückblick"
LIST_HEADING: Final = "### Baustellen im Jahr"
# The year the ledger was started in: its first row is of 24.05.2026, the
# first commit of the Baustellen cache. Every later year is recorded from
# 1 January, and a cache write without a site writes no row, so the first
# row of a later year does not say when recording began (review of
# 2026-11-10); only for this year do page and facts name the first day.
FIRST_LEDGER_YEAR: Final = 2026
# Rows of the list. A docs page may hold 700 lines
# (tests/test_file_line_limit.py) and the year page carries five more
# sections; the 2026 ledger has 19 sites. The section says how many it
# left out.
MAX_LISTED: Final = 120
# Distinct description texts kept per site, newest first. A prolonged site
# repeats its text under every new guid; three keep the changes of a year
# without carrying one text many times.
MAX_DESCRIPTIONS: Final = 3
# The ledger writer caps title, district and measure at 200 characters
# (``_field`` in src/utils/stats_baustellen.py, through
# ``_CSV_TEXT_FIELD_MAX_LEN`` in src/utils/stats_sanitize.py); the same
# cap here cuts nothing the ledger holds and still bounds a hand-edited
# cell.
_CELL_MAX_LEN: Final = 200
# The guard accepts at most 1,500 characters (year_review.MAX_REVIEW_CHARS);
# the bound here only keeps a caller's unchecked text from filling the page.
_REVIEW_MAX_LEN: Final = 2000
LICENCE_URL: Final = "https://creativecommons.org/licenses/by/4.0/"
# NOTICE.md, table "Quelle": the attribution of the Stadt Wien data, verbatim.
SOURCE_ATTRIBUTION: Final = "Datenquelle: Stadt Wien – data.wien.gv.at"

# The 23 districts of Vienna in their order: index 0 is the 1st district.
# The ledger holds the number as the city writes it ("3"); the page and the
# review name the district.
DISTRICT_NAMES: Final[tuple[str, ...]] = (
    "Innere Stadt", "Leopoldstadt", "Landstraße", "Wieden", "Margareten",
    "Mariahilf", "Neubau", "Josefstadt", "Alsergrund", "Favoriten",
    "Simmering", "Meidling", "Hietzing", "Penzing", "Rudolfsheim-Fünfhaus",
    "Ottakring", "Hernals", "Währing", "Döbling", "Brigittenau",
    "Floridsdorf", "Donaustadt", "Liesing",
)
_DISTRICT_NUMBER_RE: Final = re.compile(r"(?<!\d)\d{1,4}(?!\d)")
# How the facts block names a measure (see _measure_label): words, spaces
# and hyphens only, and not longer than this.
_MEASURE_NAME_RE: Final = re.compile(r"[^\W\d_]+(?:[ -]+[^\W\d_]+)*")
_MEASURE_NAME_MAX_LEN: Final = 60
MEASURE_NOT_GIVEN: Final = "ohne Angabe"
MEASURE_OTHER: Final = "andere Angabe"
# The months as an Austrian text names them; index 0 is January.
MONTH_NAMES: Final[tuple[str, ...]] = (
    "Jänner", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
)


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


# ---- district and period as the page writes them ------------------------------


def district_numbers(district: str) -> tuple[int, ...]:
    """The districts (1 to 23) a ledger cell names, in its order.

    The city writes the number ("3", "2, 3"); a postal code ("1030") names
    the same district. Anything else names none.
    """
    found: list[int] = []
    for raw in _DISTRICT_NUMBER_RE.findall(district):
        number = int(raw)
        if 1010 <= number <= 1230 and number % 10 == 0:
            number = (number - 1000) // 10
        if 1 <= number <= len(DISTRICT_NAMES) and number not in found:
            found.append(number)
    return tuple(found)


def district_label(district: str) -> str:
    """``3`` as ``3., Landstraße``; a cell without a district number as written."""
    numbers = district_numbers(district)
    if not numbers:
        return district
    return "; ".join(f"{number}., {DISTRICT_NAMES[number - 1]}" for number in numbers)


def _day(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def period_text(site: BaustelleSite) -> str:
    """The site's period: ``25.05.2026 – 06.09.2026``, an open end as ``offen``."""
    if site.starts is not None:
        return f"{_day(site.starts)} – {_day(site.ends) if site.ends is not None else 'offen'}"
    if site.ends is not None:
        return f"bis {_day(site.ends)}"
    return "nicht angegeben"


# ---- what the script counts ---------------------------------------------------


def recorded_since(sites: Sequence[BaustelleSite], year: int) -> date | None:
    """The Vienna day the record of *year* begins, where the ledger can say it.

    That is the first sighting of the year, and only for
    :data:`FIRST_LEDGER_YEAR`: there the ledger began with the first cache
    commit. In a later year the first sighting is merely the day the first
    site with consequences appeared, while recording ran from 1 January.
    ``to_vienna``, as in :func:`months_touched`: a naive time stamp is
    Vienna time, not the time zone of the machine that renders the page.
    """
    if year != FIRST_LEDGER_YEAR or not sites:
        return None
    return to_vienna(min(site.first_seen for site in sites)).date()


def _measure_label(measure: str) -> str:
    """The measure as the facts block may name it.

    The facts block stands in the prompt outside the frame that marks the
    city's texts as data, and the measure is the city's free text. It is
    named there only when it is words, spaces and hyphens and at most
    :data:`_MEASURE_NAME_MAX_LEN` characters ("Straßenbau - Radweg
    Neuherstellung"); anything else is counted without its wording.
    """
    name = _one_line(measure)
    if not name:
        return MEASURE_NOT_GIVEN
    if len(name) <= _MEASURE_NAME_MAX_LEN and _MEASURE_NAME_RE.fullmatch(name):
        return name
    return MEASURE_OTHER


def _measure_order(item: tuple[str, int]) -> tuple[int, int, str]:
    """Most frequent first, then by name; the two labels without a wording last."""
    label, count = item
    last = (MEASURE_OTHER, MEASURE_NOT_GIVEN)
    return (last.index(label) + 1 if label in last else 0, -count, label)


def _tally(head: str, counted: Sequence[tuple[str, int]], empty: str) -> tuple[str, tuple[int, ...]]:
    """One facts line ``head: name: count, …`` (or *empty*) with the counts it prints."""
    text = ", ".join(f"{name}: {count}" for name, count in counted) or empty
    return f"{head}: {text}", tuple(count for _, count in counted)


def _fact_lines(sites: Sequence[BaustelleSite], year: int) -> list[tuple[str, tuple[int, ...]]]:
    """Every line of the facts block, each with the counts it prints.

    One list feeds :func:`facts_block` and :func:`fact_counts`, so the
    guard's set of counts cannot drift from what the model was given. The
    year and the day the record begins are no counts.
    """
    months = Counter(site.starts.month for site in sites if site.starts is not None and site.starts.year == year)
    districts = Counter(number for site in sites for number in district_numbers(site.district))
    measures = Counter(_measure_label(site.measure) for site in sites)
    before = sum(1 for site in sites if site.starts is not None and site.starts.year < year)
    after = sum(1 for site in sites if site.starts is not None and site.starts.year > year)
    unknown = sum(1 for site in sites if site.starts is None)
    lines: list[tuple[str, tuple[int, ...]]] = [
        (f"- Jahr: {year}", ()),
        (f"- Baustellen mit Folgen für den öffentlichen Verkehr: {len(sites)}", (len(sites),)),
    ]
    since = recorded_since(sites, year)
    if since is not None and since > date(year, 1, 1):
        # Without it the model takes the counts for the whole year (review of 2026-11-10).
        lines.append((f"- Erfasst seit: {_day(since)} (Baustellen, die davor endeten, fehlen in den Zahlen)", ()))
    lines.append(_tally(
        f"- Beginn im Jahr {year}, je Monat", [(MONTH_NAMES[month - 1], months[month]) for month in sorted(months)], "keiner",
    ))
    lines.append((f"- Beginn vor {year}: {before}", (before,)))
    if after:
        lines.append((f"- Beginn nach {year}: {after}", (after,)))
    if unknown:
        lines.append((f"- Beginn nicht angegeben: {unknown}", (unknown,)))
    by_count = sorted(districts, key=lambda number: (-districts[number], number))
    lines.append(_tally(
        "- Baustellen je Bezirk",
        [(f"{number}. Bezirk ({DISTRICT_NAMES[number - 1]})", districts[number]) for number in by_count],
        "keine Angabe",
    ))
    lines.append(_tally("- Baustellen je Maßnahme", sorted(measures.items(), key=_measure_order), "keine Angabe"))
    return lines


def facts_block(sites: Sequence[BaustelleSite], *, year: int) -> str:
    """The facts the script counted: the year, the sites, their starts per month, districts and measures.

    The model gets them with the prompt, and the guard accepts their
    numbers besides those of the data: a count the model could only guess
    is given to it instead. The prompt asks which measures were frequent,
    so they are counted here too; without that line the true "5 Gleisbau"
    of 2026 was rejected because no 5 stood anywhere in the data. For the
    year the ledger began in, one line says since when the sites are
    recorded (:func:`recorded_since`).
    """
    return "\n".join(line for line, _ in _fact_lines(sites, year))


def fact_counts(sites: Sequence[BaustelleSite], *, year: int) -> frozenset[int]:
    """Every count the facts block prints, from the same computation as its lines.

    The total, the starts per month, before, after and unknown, the sites
    per district and per measure. The guard of ``year_review`` takes a
    number in digits directly before "Baustelle(n)" only from this set: a
    number that merely stands somewhere in the data ("21., Floridsdorf")
    is no count of sites. The year, the number of a district and the day
    the record begins are not in it unless they equal a count.
    """
    return frozenset(count for _, counts in _fact_lines(sites, year) for count in counts)


def months_touched(sites: Sequence[BaustelleSite], year: int) -> set[int]:
    """The months of *year* that the period of at least one site touches.

    A site without a start counts from the day it was first recorded, a
    site without an end to the end of the year.
    """
    first_day, last_day = date(year, 1, 1), date(year, 12, 31)
    months: set[int] = set()
    for site in sites:
        start = max(site.starts or to_vienna(site.first_seen).date(), first_day)
        end = min(site.ends or last_day, last_day)
        if start <= end:
            months.update(range(start.month, end.month + 1))
    return months


# ---- the section ----------------------------------------------------------


def _cell(text: str) -> str:
    """Ledger text as a Markdown table cell; an empty one as a dash.

    A backtick becomes an apostrophe, as in ``safe_markdown_codespan``:
    kramdown pairs backticks before it reads the escapes, so two cells
    with one escaped backtick each ran together into one cell.
    """
    clean = normalise_markdown_text(text, max_len=_CELL_MAX_LEN).replace("`", "'")
    return escape_markdown_cell(clean) if clean else "–"


def _count_sentence(count: int, year: int) -> str:
    if count == 1:
        return f"Im Jahr {year} ist **eine** solche Baustelle erfasst."
    return f"Im Jahr {year} sind **{count}** solche Baustellen erfasst."


def _review_lines(review: str) -> list[str]:
    """The review in words with the line that says who wrote and who checked it.

    Escaped like a table cell: kramdown makes a table of a paragraph line
    with a bare "|". The guard rejects that character before a text is
    stored; this is the second line of defence for a caller's own text.
    """
    return [
        REVIEW_HEADING,
        "",
        escape_markdown_cell(normalise_markdown_text(review, max_len=_REVIEW_MAX_LEN)),
        "",
        (
            "_Dieser Rückblick wurde automatisch mit Gemini (Google) aus den Texten "
            "der Stadt Wien zusammengefasst und von einem festen Skript gegen diese "
            "Texte geprüft. Anzahl und Liste stammen direkt aus den Daten._"
        ),
        "",
    ]


def _list_lines(sites: Sequence[BaustelleSite]) -> list[str]:
    """The table of the sites, capped at :data:`MAX_LISTED` rows, the rest counted."""
    lines = [
        LIST_HEADING,
        "",
        "| Ort | Zeitraum | Bezirk | Maßnahme |",
        "| --- | --- | --- | --- |",
    ]
    for site in sites[:MAX_LISTED]:
        lines.append(
            f"| {_cell(site.title)} | {_cell(period_text(site))} "
            f"| {_cell(district_label(site.district))} | {_cell(site.measure)} |"
        )
    lines.append("")
    rest = len(sites) - MAX_LISTED
    if rest > 0:
        noun = "weitere Baustelle ist" if rest == 1 else "weitere Baustellen sind"
        lines += [
            f"_Die Liste zeigt die ersten {MAX_LISTED} nach Beginn; {rest} {noun} hier nicht aufgeführt._",
            "",
        ]
    return lines


def render_baustellen_section(
    sites: Sequence[BaustelleSite],
    *,
    year: int,
    review: str | None = None,
    pending: bool = False,
) -> list[str]:
    """The section "Baustellen mit Folgen für den öffentlichen Verkehr" as Markdown lines.

    Empty without *sites*: a year without ledger rows gets no section.
    Otherwise, in this order: what the section covers, how the sites are
    told apart and, for the year the ledger began in, since when they were
    recorded (:func:`recorded_since`), the count, the review in words when
    *review* is given (an accepted text of ``year_review``; labelled as
    written by Gemini and checked by script), or, with *pending* and no
    review, one line that a review follows, then the list of the sites and
    the attribution ``NOTICE.md`` asks for.

    The kinds of consequences are examples ("etwa"): the rule
    (``mentions_oepnv`` in ``src/providers/baustellen.py``) takes any
    sentence that names public transport as affected, and 3 of the 19
    sites of 2026 are listed for a suspended tram service alone. The
    sentences on names say how :func:`load_sites` counts: a changed period
    counts once, the same name again when it is entered after the period in
    force (the one shown) had ended, for a separate period
    (:func:`_sites_of_title`), and a site under two names twice; the city
    entered one site of 2026 under two titles, and nothing in the data
    says the two are one.

    Count and list never come from the model (owner's decision of
    2026-11-10, point 4): the deterministic part is always present and is
    the fallback.
    """
    if not sites:
        return []
    review_text = (review or "").strip()
    intro = (
        "Hier stehen die Baustellen aus den Daten der Stadt Wien, deren Text eine "
        "Folge für den öffentlichen Verkehr nennt, etwa eine verlegte oder aufgelassene "
        "Haltestelle, eine umgeleitete oder kurzgeführte Linie, einen eingestellten "
        "Straßenbahnbetrieb oder Ersatzbusse. Gezählt wird nach dem Namen, unter dem "
        "die Stadt Wien eine Baustelle führt. Ändert die Stadt den Zeitraum, bevor der "
        "alte zu Ende ist, gilt der neue; trägt sie denselben Namen erst nach dem Ende "
        "des geltenden Zeitraums für einen Zeitraum ein, der an diesen weder anschließt "
        "noch ihn überschneidet, zählt er noch einmal. Trägt sie eine Baustelle unter "
        "einem anderen Namen neu ein, zählt sie unter jedem Namen einmal."
    )
    since = recorded_since(sites, year)
    if since is not None:
        intro += f" Die Aufzeichnung für {year} beginnt am {_day(since)}."
    lines = [
        SECTION_HEADING,
        "",
        intro,
        "",
        _count_sentence(len(sites), year),
        "",
    ]
    if review_text:
        lines += _review_lines(review_text)
    elif pending:
        lines += ["_Ein kurzer Rückblick in Worten wird hier ergänzt._", ""]
    lines += _list_lines(sites)
    edited = (
        "gekürzt, vereinheitlicht und im Rückblick maschinell zusammengefasst"
        if review_text
        else "gekürzt und vereinheitlicht"
    )
    lines.append(
        f"> {SOURCE_ATTRIBUTION}. Die Texte der Stadt Wien sind hier bearbeitet "
        f"({edited}). Lizenz: [CC BY 4.0]({LICENCE_URL})."
    )
    return lines
