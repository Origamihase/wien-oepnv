"""The Baustellen section of a finished year's page, read from the ledger.

Owner's decision (Michael, 2026-11-10 17:44 Europe/Vienna), point 4: "The
year's Baustellen (roadworks) are summarised by Gemini." The summary in
words is the second half of that (``src/markdown_stats/year_review.py``);
this module is the first half and the fallback: the count and the list of
the year's sites come straight from ``data/stats/baustellen_<Jahr>.csv``
and stand on the page whether or not a review exists.

The ledger holds one row per guid of the Stadt Wien data. That guid is a
hash of title, start and end (``src/providers/baustellen_events.py``), so
a site whose end was moved appears under a new guid; in 2026 the feed
showed 191 guids for 66 titles. :func:`load_sites` therefore merges the
rows of one title into one site.

Licence: the texts are the city's, CC BY 4.0, and appear edited
(``NOTICE.md``, "Generierte Feeds"): the section ends with the source, the
note on the editing and the licence link.
"""
from __future__ import annotations

import csv
import io
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
from src.utils.stats import VIENNA_TZ, stats_path, to_vienna
from src.utils.stats_baustellen import BAUSTELLEN_HEADER
from src.utils.text import (
    escape_markdown,
    escape_markdown_cell,
    normalise_markdown_text,
)
from src.utils.vienna_clock import PLAUSIBLE_YEARS

LEDGER_KIND: Final = "baustellen"
SECTION_HEADING: Final = "## Baustellen mit Folgen für den öffentlichen Verkehr"
REVIEW_HEADING: Final = "### Rückblick"
LIST_HEADING: Final = "### Baustellen im Jahr"
# Rows of the list. A docs page may hold 700 lines
# (tests/test_file_line_limit.py) and the year page carries five more
# sections; 2026 had 66 sites. The section says how many it left out.
MAX_LISTED: Final = 120
# Distinct description texts kept per site, newest first. A prolonged site
# repeats its text under every new guid; three keep the changes of a year
# without carrying one text many times.
MAX_DESCRIPTIONS: Final = 3
# The ledger writer caps title, district and measure at 200 characters
# (shared brief, "Ledgers"); the same cap here cuts nothing the ledger
# holds and still bounds a hand-edited cell.
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
    "Innere Stadt",
    "Leopoldstadt",
    "Landstraße",
    "Wieden",
    "Margareten",
    "Mariahilf",
    "Neubau",
    "Josefstadt",
    "Alsergrund",
    "Favoriten",
    "Simmering",
    "Meidling",
    "Hietzing",
    "Penzing",
    "Rudolfsheim-Fünfhaus",
    "Ottakring",
    "Hernals",
    "Währing",
    "Döbling",
    "Brigittenau",
    "Floridsdorf",
    "Donaustadt",
    "Liesing",
)
_DISTRICT_NUMBER_RE: Final = re.compile(r"(?<!\d)\d{1,4}(?!\d)")
# The months as an Austrian text names them; index 0 is January.
MONTH_NAMES: Final[tuple[str, ...]] = (
    "Jänner", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
)


@dataclass(frozen=True)
class BaustelleSite:
    """One site of the year: every ledger row of one title, merged."""

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


def _ledger_moment(value: str) -> datetime | None:
    """The row's time stamp in Vienna time; a naive one is read as Vienna time."""
    try:
        parsed = datetime.fromisoformat(value.strip())
        if not PLAUSIBLE_YEARS[0] <= parsed.year <= PLAUSIBLE_YEARS[1]:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=VIENNA_TZ)
        return parsed.astimezone(VIENNA_TZ)
    except (ValueError, OverflowError):
        return None


def _parse_row(cells: Sequence[str]) -> _LedgerRow | None:
    """A ledger row, or ``None`` for one without time stamp, guid or title."""
    if len(cells) != len(BAUSTELLEN_HEADER):
        return None
    row = dict(zip(BAUSTELLEN_HEADER, cells, strict=True))
    seen = _ledger_moment(row["timestamp"])
    guid = row["guid"].strip()
    title = _one_line(row["title"])
    if seen is None or not guid or not title:
        return None
    return _LedgerRow(
        seen=seen,
        guid=guid,
        title=title,
        starts=_ledger_date(row["starts_at"]),
        ends=_ledger_date(row["ends_at"]),
        district=_one_line(row["district"]),
        measure=_one_line(row["measure"]),
        description=_one_line(row["description"]),
    )


def _split_line(line: str) -> list[str] | None:
    """The cells of one physical ledger line; ``None`` when it is no CSV record.

    ``strict``: a quote that is opened and not closed on the line is an
    error. Without it the csv module would return the first half of a
    record that a hand edit broke over two lines as if it were whole.
    *line* comes out of the size-capped read in :func:`_read_rows`; no file
    handle reaches the csv module (tests/test_sentinel_csv_size_bomb.py).
    """
    try:
        return next(csv.reader(io.StringIO(line), strict=True), None)
    except csv.Error:
        return None


def _read_rows(path: Path) -> Iterator[_LedgerRow]:
    """Every readable row of the ledger at *path*.

    The file is read through the size cap of the other ledgers and taken
    apart line by line: a ledger row is one physical line (the writer
    collapses line breaks, and ``data/stats/*.csv merge=union`` in
    ``.gitattributes`` merges by line), so one damaged line, an unbalanced
    quote for instance, costs that line and not the rest of the file. A
    line that repeats the header (two branches created the file) is
    skipped, a file that starts with another header is not read at all.
    """
    raw = read_capped_text(path, max_bytes=MAX_CSV_BYTES, label="stats CSV", logger=LOGGER)
    if raw is None:
        return
    lines = [line.rstrip("\r") for line in raw.split("\n")]
    lines = [line for line in lines if line.strip()]
    if not lines:
        return
    if tuple(_split_line(lines[0]) or ()) != BAUSTELLEN_HEADER:
        LOGGER.warning(
            "Stats-Datei %s hat unerwarteten Header — überspringe.",
            sanitize_log_arg(str(path)),
        )
        return
    skipped = 0
    for line in lines[1:]:
        cells = _split_line(line)
        if cells is not None and tuple(cells) == BAUSTELLEN_HEADER:
            continue
        row = _parse_row(cells) if cells is not None else None
        if row is None:
            skipped += 1
            continue
        yield row
    if skipped:
        LOGGER.warning(
            "Stats-Datei %s: %d unlesbare Zeile(n) übersprungen.",
            sanitize_log_arg(str(path)),
            skipped,
        )


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
    """What makes two rows the same site: the title, case and spacing aside."""
    return _one_line(title).casefold()


def _merge(rows: Sequence[_LedgerRow]) -> BaustelleSite:
    """One site from the rows of one title (see the module docstring).

    The period runs from the earliest start to the latest end any row
    names. Title, district and measure are the newest row's; an empty cell
    there falls back to the newest row that has one. The description
    texts are kept once each, newest first, at most
    :data:`MAX_DESCRIPTIONS`.
    """
    newest_first = sorted(rows, key=lambda row: row.seen, reverse=True)
    starts = [row.starts for row in rows if row.starts is not None]
    ends = [row.ends for row in rows if row.ends is not None]
    descriptions: list[str] = []
    for row in newest_first:
        if row.description and row.description not in descriptions:
            descriptions.append(row.description)
    return BaustelleSite(
        title=newest_first[0].title,
        starts=min(starts) if starts else None,
        ends=max(ends) if ends else None,
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
    keeps one row per guid and merges the rows of one title into one site.
    A missing, oversized or foreign file gives no sites; a damaged line is
    skipped and counted in one log line. Sorted by start (sites without
    one last), then by title.
    """
    path = stats_path(LEDGER_KIND, year, base_dir=stats_dir)
    by_title: dict[str, list[_LedgerRow]] = {}
    for row in _first_per_guid(_read_rows(path)):
        by_title.setdefault(_title_key(row.title), []).append(row)
    return sorted((_merge(rows) for rows in by_title.values()), key=_site_order)


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


def _start_months(sites: Sequence[BaustelleSite], year: int) -> str:
    counts = Counter(site.starts.month for site in sites if site.starts is not None and site.starts.year == year)
    if not counts:
        return "keiner"
    return ", ".join(f"{MONTH_NAMES[month - 1]}: {counts[month]}" for month in sorted(counts))


def _district_counts(sites: Sequence[BaustelleSite]) -> str:
    counts = Counter(number for site in sites for number in district_numbers(site.district))
    if not counts:
        return "keine Angabe"
    ordered = sorted(counts, key=lambda number: (-counts[number], number))
    return ", ".join(f"{number}. Bezirk ({DISTRICT_NAMES[number - 1]}): {counts[number]}" for number in ordered)


def facts_block(sites: Sequence[BaustelleSite], *, year: int) -> str:
    """The facts the script counted: the year, the sites, their starts per month, the districts.

    The model gets them with the prompt, and the guard accepts their
    numbers besides those of the data: a count the model could only guess
    is given to it instead.
    """
    before = sum(1 for site in sites if site.starts is not None and site.starts.year < year)
    after = sum(1 for site in sites if site.starts is not None and site.starts.year > year)
    unknown = sum(1 for site in sites if site.starts is None)
    lines = [
        f"- Jahr: {year}",
        f"- Baustellen mit Folgen für den öffentlichen Verkehr: {len(sites)}",
        f"- Beginn im Jahr {year}, je Monat: {_start_months(sites, year)}",
        f"- Beginn vor {year}: {before}",
    ]
    if after:
        lines.append(f"- Beginn nach {year}: {after}")
    if unknown:
        lines.append(f"- Beginn nicht angegeben: {unknown}")
    lines.append(f"- Baustellen je Bezirk: {_district_counts(sites)}")
    return "\n".join(lines)


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
    """Ledger text as a Markdown table cell; an empty one as a dash."""
    clean = normalise_markdown_text(text, max_len=_CELL_MAX_LEN)
    return escape_markdown_cell(clean) if clean else "–"


def _count_sentence(count: int, year: int) -> str:
    if count == 1:
        return f"Im Jahr {year} ist **eine** solche Baustelle erfasst."
    return f"Im Jahr {year} sind **{count}** solche Baustellen erfasst."


def _review_lines(review: str) -> list[str]:
    """The review in words with the line that says who wrote and who checked it."""
    return [
        REVIEW_HEADING,
        "",
        escape_markdown(normalise_markdown_text(review, max_len=_REVIEW_MAX_LEN)),
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
    Otherwise, in this order: what the section covers and since when the
    year's sites were recorded (the first sighting of the year), the count,
    the review in words when *review* is given (an accepted text of
    ``year_review``; labelled as written by Gemini and checked by script),
    or, with *pending* and no review, one line that a review follows, then
    the list of the sites and the attribution ``NOTICE.md`` asks for.

    Count and list never come from the model (owner's decision of
    2026-11-10, point 4, and the shared brief: the deterministic part is
    always present and is the fallback).
    """
    if not sites:
        return []
    # to_vienna, as in months_touched: a naive time stamp is Vienna time here,
    # not the time zone of the machine that renders the page.
    since = to_vienna(min(site.first_seen for site in sites))
    review_text = (review or "").strip()
    lines = [
        SECTION_HEADING,
        "",
        (
            "Hier stehen die Baustellen aus den Daten der Stadt Wien, deren Text eine "
            "Folge für den öffentlichen Verkehr nennt: eine verlegte oder aufgelassene "
            "Haltestelle, eine umgeleitete oder kurzgeführte Linie oder Ersatzbusse. "
            f"Die Aufzeichnung für {year} beginnt am {since.strftime('%d.%m.%Y')}."
        ),
        "",
        _count_sentence(len(sites), year),
        "",
    ]
    if review_text:
        lines += _review_lines(review_text)
    elif pending:
        lines += ["_Ein kurzer Rückblick in Worten folgt in der ersten Jännerwoche._", ""]
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
