"""Yearly ledger of the Stadt Wien Baustellen with an effect on public transport.

``data/stats/baustellen_<year>.csv`` holds one row per construction site
(guid) that the Baustellen cache contained in that Vienna year: when it was
first seen, its title, its planned period, district, measure and its text.
The page of a finished year (``docs/statistik-<year>.md``) lists these sites
and has them summarised (owner's decision of 2026-11-10, point 4: "the
year's Baustellen are summarised"); until this ledger existed nothing
durable in the working tree kept a site's title or text, only the count in
``stoerungen_<year>.csv``.

Why the row is written at the cache write and not in the feed build, where
every other statistics row is written: since 2026-10-09 a site that a
running Wiener Linien notice already reports is dropped from the feed (the
twins rule, ``src/feed_build/wl_notices.py``), so the feed would record
almost none. The yearly review covers every site that passed the owner's
rule "only Baustellen with consequences for public transport" (2026-10-06,
``src/providers/baustellen.py``), and that rule is applied exactly where
``scripts/update_baustellen_cache.py`` writes the cache.

Contract, the same as for the writers in :mod:`src.utils.stats`:

* Append-only, one file per Vienna year of the write. A site that is still
  in the cache after New Year gets a row in the new year's file as well:
  each year's file stands for itself, and the page of a year lists the
  sites that were under way in it. A site whose last day lies before
  1 January of the year of the write gets no row: the city's answer can
  still hold a site on the day after its end (seen on 2026-09-05 for a site
  that ended on 2026-09-04), and at New Year such a site was not under way
  in the new year (review of 2026-11-10).
* A row holds the site's text as it stood when its guid was first seen in
  that year. A text the city changes later under the same guid is not
  entered again; a moved start or end gives a new guid and so a new row
  with the text of that day. The 61 rows of 2026 (19 sites) were backfilled
  on 2026-11-10 from the history of the cache: each carries the last text
  the cache held for its guid, under the time of the guid's first cache
  commit.
* One record is one physical line (every text field is collapsed to a
  single line), so the ``merge=union`` rule for ``data/stats/*.csv`` keeps
  both sides' rows. A union merge or two runs at the same moment can repeat
  a line: readers treat a repeated guid as one site.
* A row stays below :data:`MAX_ROW_BYTES`, so a single ``write()`` of it is
  atomic against a concurrent appender (``PIPE_BUF``, 4 KiB).
* Only the named fields of the cache event are stored. The contact-person
  fields of the raw WFS layer are not part of a cache event and never
  reach this file.
* Best effort: a failure is logged at WARNING and never raised; the cache
  update goes on without the row and the next run tries again.
"""
from __future__ import annotations

import csv
import io
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final, NamedTuple

from src.providers.baustellen_events import _format_description
from src.utils.files import read_capped_text
from src.utils.logging import sanitize_log_arg
from src.utils.stats import (
    VIENNA_TZ,
    WEEKDAY_LABELS,
    _append_row,
    stats_path,
    to_vienna,
)
from src.utils.stats_sanitize import (
    _CSV_CONTROL_CHARS_RE,
    _CSV_FORMULA_PREFIXES,
    _CSV_TEXT_FIELD_MAX_LEN,
    _sanitize_csv_text_field,
)
from src.utils.vienna_clock import PLAUSIBLE_YEARS

LOGGER = logging.getLogger("utils.stats_baustellen")

# Pinned like the headers in ``src/utils/stats.py``: the reader of the yearly
# review imports this tuple, so writer and reader cannot drift apart.
BAUSTELLEN_HEADER: Final = (
    "timestamp",
    "weekday",
    "hour",
    "guid",
    "title",
    "starts_at",
    "ends_at",
    "district",
    "measure",
    "description",
)
_TIMESTAMP_COLUMN: Final = BAUSTELLEN_HEADER.index("timestamp")
_GUID_COLUMN: Final = BAUSTELLEN_HEADER.index("guid")
_TITLE_COLUMN: Final = BAUSTELLEN_HEADER.index("title")
_ENDS_COLUMN: Final = BAUSTELLEN_HEADER.index("ends_at")

# The longest description measured in the 2026 history has 1,952 characters
# (median 517), so 2,000 keeps every real text whole. The other text fields
# keep the 200 characters of ``_sanitize_csv_text_field``; the longest title
# of that history has 101 characters.
MAX_DESCRIPTION_CHARS: Final = 2000
# Below ``PIPE_BUF`` (4,096 bytes), see the module docstring. 2,000 umlauts
# alone are 4,000 bytes, so the description is shortened further when the
# encoded row would reach this size.
MAX_ROW_BYTES: Final = 4000
# Read cap, same value and reason as ``MAX_STAMMSTRECKE_CSV_BYTES``: a year
# of real rows is well below 1 MiB (61 rows for 19 sites in 2026, 62,523
# bytes), and a file that was planted or grew by mistake must not be
# buffered whole.
MAX_BAUSTELLEN_CSV_BYTES: Final = 16 * 1024 * 1024

_ELLIPSIS: Final = "…"
# A spreadsheet that saves the file as "UTF-8 with BOM" puts this character
# before the header.
_BOM: Final = "\ufeff"
# What the provider writes as the description of a site that has no text,
# no date, no measure and no district ("Baustelle ohne weitere Angaben").
# It is the provider's wording, not the city's (review of 2026-11-10), so
# the ledger stores an empty text instead. The provider keeps the text
# inline in ``_format_description``; asking that function for a site
# without any field gives exactly its text, so the two cannot drift apart.
NO_DETAILS_TEXT: Final = _format_description({}, None, None)


def _one_line(value: object) -> str:
    """Return *value* as one line with single spaces; ``""`` for a non-string.

    Whitespace is collapsed before the invisible characters are removed, so
    a line separator between two words becomes a space instead of gluing
    them; the second pass closes the gap an invisible character leaves
    between two spaces. A lone surrogate (possible in JSON) is replaced
    first: the UTF-8 file could not encode it and the row would be lost.
    """
    if not isinstance(value, str):
        return ""
    text = value.encode("utf-8", errors="replace").decode("utf-8")
    text = _CSV_CONTROL_CHARS_RE.sub("", " ".join(text.split()))
    return " ".join(text.split())


def _shorten(text: str, keep: int) -> str:
    """The first *keep* characters of *text*, marked as shortened."""
    return text[: max(keep, 0)].rstrip() + _ELLIPSIS


def _field(value: object) -> str:
    """A short text field: one line, 200 characters, CSV formula guard.

    A longer value ends in "…" like a shortened description: the cut is
    made here, before the shared sanitiser would make it without a mark
    (review of 2026-11-10; the reader's title key ignores a final "…").
    """
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    text = _one_line(value)
    if len(text) > _CSV_TEXT_FIELD_MAX_LEN:
        text = _shorten(text, _CSV_TEXT_FIELD_MAX_LEN - 1)
    return _sanitize_csv_text_field(text)


# The date lines the provider appends to a site's text from its own fields
# (``_format_description`` in ``src/providers/baustellen_events.py``), as
# the last line of a text: from a line start to the end of the text.
_GENERATED_DATE_TAIL: Final = re.compile(
    r"^[ \t]*(?:Beginn|Geplant bis): \d{2}\.\d{2}\.\d{4} \d{2}:\d{2} Uhr\Z",
    re.MULTILINE,
)


def _generated_tail(text: str, context_lines: Sequence[str]) -> int | None:
    """Where the provider's last generated line begins in *text*, or ``None``.

    *text* has no whitespace at its end. A generated line is a date line in
    the provider's format or one of *context_lines* ("Maßnahme: …",
    "Bezirk: …" with the event's own values), and it counts only when it
    begins a line: the same words at the end of a sentence of the city stay.
    """
    match = _GENERATED_DATE_TAIL.search(text)
    if match:
        return match.start()
    for line in context_lines:
        start = len(text) - len(line)
        before = text[:start].rstrip(" \t")
        if text.endswith(line) and (not before or before.endswith("\n")):
            return start
    return None


def _own_text(event: Mapping[str, Any]) -> object:
    """The event's description without the provider's generated last lines.

    The cache description ends with "Beginn: … / Geplant bis: … /
    Maßnahme: … / Bezirk: …". The ledger holds those values in columns of
    their own; repeated in the text they would take room from the city's
    sentences and would be read as part of them by the yearly review.
    Only whole lines at the end are cut, and only the ones the provider
    writes: the two date lines in its format, and the measure and district
    lines when they say what the event's ``context`` says. A sentence of
    the city that begins with one of these words stays.

    The end of the text is compared, not single lines, so a measure or a
    district that itself holds a line break is recognised too. A site
    without any text of the city keeps nothing: an empty description, not
    the first generated line and not the provider's
    :data:`NO_DETAILS_TEXT` (review of 2026-11-10).
    """
    value = event.get("description")
    if not isinstance(value, str):
        return value
    if value.strip() == NO_DETAILS_TEXT:
        return ""
    context = event.get("context")
    context_lines: list[str] = []
    if isinstance(context, Mapping):
        context_lines = [
            f"{label}: {context[key]}".rstrip()
            for label, key in (("Maßnahme", "measure"), ("Bezirk", "district"))
            if context.get(key)
        ]
    text = value.rstrip()
    while text:
        start = _generated_tail(text, context_lines)
        if start is None:
            break
        text = text[:start].rstrip()
    return text


def _description(value: object) -> str:
    """The site's text: one line, formula guard, :data:`MAX_DESCRIPTION_CHARS`."""
    text = _one_line(value)
    if text.startswith(_CSV_FORMULA_PREFIXES):
        text = "'" + text
    if len(text) > MAX_DESCRIPTION_CHARS:
        text = _shorten(text, MAX_DESCRIPTION_CHARS - 1)
    return text


def _vienna_date(value: object) -> str:
    """The Vienna calendar date of an event instant as ``YYYY-MM-DD``, or ``""``.

    The cache holds ISO strings (``serialize_for_cache``); an event straight
    from the provider holds datetimes. Anything else, and a value that
    cannot be read, gives an empty field rather than a lost row.
    """
    moment: object = value
    if isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.strip())
        except ValueError:
            return ""
    if isinstance(moment, datetime):
        try:
            return to_vienna(moment).date().isoformat()
        except (OverflowError, ValueError):
            return ""
    if isinstance(moment, date):
        return moment.isoformat()
    return ""


def _encoded_size(row: tuple[str, ...]) -> int:
    """Bytes of *row* as ``_append_row`` writes it (same ``csv`` dialect)."""
    buffer = io.StringIO()
    csv.writer(buffer).writerow(row)
    return len(buffer.getvalue().encode("utf-8"))


def _fit_row(head: tuple[str, ...], description: str) -> tuple[str, ...]:
    """Return ``head + (description,)`` below :data:`MAX_ROW_BYTES`.

    The description is the only field that can make a row too long: the
    four text fields of *head* have at most 201 characters of at most four
    bytes each, about 3,300 bytes with the fixed fields and the quoting. A
    description that does not fit keeps the longest beginning that does
    (binary search; the size never falls when a character is added).
    """
    row = (*head, description)
    if _encoded_size(row) < MAX_ROW_BYTES:
        return row
    fitting: tuple[str, ...] = (*head, "")
    low, high = 0, len(description) - 1
    while low <= high:
        keep = (low + high) // 2
        candidate = (*head, _shorten(description, keep))
        if _encoded_size(candidate) < MAX_ROW_BYTES:
            fitting = candidate
            low = keep + 1
        else:
            high = keep - 1
    return fitting


def baustelle_row(event: Mapping[str, Any], *, seen_at: datetime) -> tuple[str, ...] | None:
    """Return the ledger row of one Baustellen cache event, or ``None``.

    *seen_at* is the moment the site was seen (the cache write; a backfill
    passes the moment from the history), it gives ``timestamp``, ``weekday``
    and ``hour`` in Vienna time like in the other ledgers. ``None`` when the
    event has no usable guid or title: without the guid a site cannot be
    recognised again, without the title the yearly list has nothing to name.

    Pure: no clock, no file. The guid is stored in the same cleaned form
    that :func:`known_guids` reads back, so the two always compare equal.
    """
    raw_guid, raw_title = event.get("guid"), event.get("title")
    guid = _field(raw_guid) if isinstance(raw_guid, str) else ""
    title = _field(raw_title) if isinstance(raw_title, str) else ""
    if not guid or not title:
        return None
    when = to_vienna(seen_at)
    context = event.get("context")
    if not isinstance(context, Mapping):
        context = {}
    head = (
        when.isoformat(timespec="seconds"),
        WEEKDAY_LABELS[when.weekday()],
        f"{when.hour:02d}",
        guid,
        title,
        _vienna_date(event.get("starts_at")),
        _vienna_date(event.get("ends_at")),
        _field(context.get("district")),
        _field(context.get("measure")),
    )
    return _fit_row(head, _description(_own_text(event)))


def split_ledger_line(line: str) -> list[str] | None:
    """One physical line as CSV fields; ``None`` when it is not a CSV record.

    ``strict``: a quotation mark that is not closed on the line, or text
    after the closing one, is no record. Without it the csv module would
    return the first half of a record that a hand edit broke over two
    lines as if it were whole. *line* is a string out of a size-capped
    read; no file handle reaches the csv module
    (tests/test_sentinel_csv_size_bomb.py).
    """
    try:
        return next(csv.reader(io.StringIO(line), strict=True), None)
    except csv.Error:
        return None


def ledger_moment(value: str) -> datetime | None:
    """A row's time stamp in Vienna time; a naive one is read as Vienna time.

    ``None`` for a value that does not read or lies outside
    :data:`PLAUSIBLE_YEARS`: no ledger row can have been written then.
    """
    try:
        parsed = datetime.fromisoformat(value.strip())
        if not PLAUSIBLE_YEARS[0] <= parsed.year <= PLAUSIBLE_YEARS[1]:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=VIENNA_TZ)
        return parsed.astimezone(VIENNA_TZ)
    except (ValueError, OverflowError):
        return None


class SiteLine(NamedTuple):
    """A ledger line that holds a site: its time stamp and its ten cells."""

    seen: datetime
    cells: tuple[str, ...]


def site_line(line: str) -> SiteLine | None:
    """The site on one physical ledger line, or ``None`` when it holds none.

    The one test of a ledger line, for the writer here and for the reader
    of the year page (``_read_rows`` in
    ``src/markdown_stats/baustellen_sites.py`` imports it): a CSV record of
    exactly the ten columns, not the header, with a time stamp that reads
    (:func:`ledger_moment`), a guid and a title. A line the reader skips
    must not count as known to the writer, or its site would be missing
    from the year page and never be entered again; a line the reader
    lists must count, or the site would be entered on every run (review
    of 2026-11-10).

    A U+FFFD in a cell does not decide anything: it is either a byte that
    was no UTF-8 (both sides read with ``errors="replace"``) or the city's
    own character, and the file cannot tell the two apart. A line that
    still reads as a whole record keeps its site, the character shown as
    it is.
    """
    cells = split_ledger_line(line)
    if cells is None or len(cells) != len(BAUSTELLEN_HEADER) or tuple(cells) == BAUSTELLEN_HEADER:
        return None
    seen = ledger_moment(cells[_TIMESTAMP_COLUMN])
    if seen is None or not cells[_GUID_COLUMN].strip() or not cells[_TITLE_COLUMN].strip():
        return None
    return SiteLine(seen, tuple(cells))


class LedgerLines(NamedTuple):
    """What a ledger text holds: its sites and the count of lines that hold none."""

    sites: tuple[SiteLine, ...]
    unreadable: int


def read_ledger_text(text: str) -> LedgerLines | None:
    """The sites of a ledger text, line by line; ``None`` for a foreign file.

    *text* is the whole file, read with ``errors="replace"``. A byte order
    mark and blank lines before the header are passed over (a spreadsheet
    saves the file that way); a file whose first line with content is not
    :data:`BAUSTELLEN_HEADER` gives ``None``. Every later line is tested on
    its own with :func:`site_line`, so a torn or hand-edited line spoils
    only itself. Blank lines and the header again (two branches created
    the file, a union merge kept both) are not counted; every other line
    without a site is.
    """
    lines = [line.rstrip("\r") for line in text.removeprefix(_BOM).split("\n")]
    filled = [line for line in lines if line.strip()]
    if not filled or tuple(split_ledger_line(filled[0]) or ()) != BAUSTELLEN_HEADER:
        return None
    sites: list[SiteLine] = []
    unreadable = 0
    for line in filled[1:]:
        found = site_line(line)
        if found is not None:
            sites.append(found)
        elif tuple(split_ledger_line(line) or ()) != BAUSTELLEN_HEADER:
            unreadable += 1
    return LedgerLines(tuple(sites), unreadable)


def _ledger_guids(path: Path) -> set[str] | None:
    """The guids in the ledger at *path*; ``None`` when it must not be extended.

    A missing or empty file is an empty ledger. ``None`` (with a WARNING)
    stands for a file that exists but cannot be read within the size cap,
    or whose first line with content is not :data:`BAUSTELLEN_HEADER`: rows
    appended to it would stand under the wrong columns, and with an
    unreadable file every run would append every site again. Such a file
    is left as it is until a person has looked at it. A byte order mark
    and empty lines before the header are passed over, as the reader of
    the year page does: a file saved once by a spreadsheet is still the
    ledger (review of 2026-11-10).

    Every line is parsed on its own (:func:`read_ledger_text`, the same
    function the reader of the year page uses). The writer guarantees one
    record per line, so a torn or hand-edited line with an open quotation
    mark spoils only itself and not the lines after it. A line counts
    exactly when the reader lists its site (:func:`site_line`): a site
    whose line is damaged is entered again, one whose line still reads is
    not, whether or not a byte of it was replaced.
    """
    text = read_capped_text(
        path,
        MAX_BAUSTELLEN_CSV_BYTES,
        errors="replace",
        label="Baustellen ledger",
        logger=LOGGER,
    )
    if text is None:
        try:
            missing = not path.exists()
        except OSError:
            # ``exists`` passes on what is not "no such file" (a folder that
            # may not be searched, a name too long): unusable, not absent.
            missing = False
        if missing:
            return set()
        LOGGER.warning(
            "Baustellen-Statistik: %s ist nicht lesbar oder größer als %d Bytes; "
            "die Datei wird nicht gelesen und nicht ergänzt.",
            sanitize_log_arg(path.name),
            MAX_BAUSTELLEN_CSV_BYTES,
        )
        return None
    if not text:
        return set()
    ledger = read_ledger_text(text)
    if ledger is None:
        LOGGER.warning(
            "Baustellen-Statistik: %s hat eine andere Kopfzeile als erwartet; "
            "die Datei wird nicht gelesen und nicht ergänzt.",
            sanitize_log_arg(path.name),
        )
        return None
    return {site.cells[_GUID_COLUMN].strip() for site in ledger.sites}


def known_guids(year: int, *, stats_dir: Path | None = None) -> set[str]:
    """Return the guids that ``baustellen_<year>.csv`` already holds.

    Empty for a missing file and for a file that cannot be used (too large,
    unreadable, different header — logged at WARNING). Malformed lines and
    lines repeated by a union merge are tolerated, see :func:`_ledger_guids`.
    """
    guids = _ledger_guids(stats_path("baustellen", year, base_dir=stats_dir))
    return guids if guids is not None else set()


def _ended_before(ends_at: str, year: int) -> bool:
    """``True`` when the Vienna date *ends_at* lies before 1 January of *year*.

    The column holds ``YYYY-MM-DD`` or nothing, so the texts compare like
    the dates. No end is an open end. So is a year below
    :data:`PLAUSIBLE_YEARS`: the reader of the year page shows such an end
    as open, and the provider does not pass one on in the first place.
    """
    return f"{PLAUSIBLE_YEARS[0]:04d}" <= ends_at < f"{year:04d}-01-01"


def _end_last_line(path: Path) -> None:
    """Give the last line of the ledger at *path* its line break if it has none.

    Ledgers are corrected by hand, and an editor can leave the last line
    open. ``_append_row`` would put the next row onto that line: one line
    with 19 fields, which neither this module nor the reader of the year
    page takes for a site, so both sites would be gone. The shared writer
    of the other ledgers stays as it is (review of 2026-11-10), so the line
    break is a write of its own, one byte, before the row. A bare ``\\n``:
    after a lone ``\\r`` it completes the pair, and both readers strip a
    ``\\r``.

    A missing or empty file has no line to end. Any other failure (folder,
    permissions) is met by ``_append_row`` right after, which reports it.
    """
    try:
        with path.open("rb") as handle:
            if handle.seek(0, io.SEEK_END) == 0:
                return
            handle.seek(-1, io.SEEK_END)
            if handle.read(1) == b"\n":
                return
        with path.open("ab") as handle:
            handle.write(b"\n")
    except OSError:
        return


def _append_new(path: Path, event: object, when: datetime, known: set[str]) -> bool | None:
    """Append *event* unless its guid is in *known* or it ended before the year.

    ``True``: a row was written (and its guid added to *known*, which also
    covers a site that both WFS layers list). ``False``: nothing to write.
    ``None``: the file refused the row; the caller stops, because the cause
    (disk, permissions) is the same for the next row.
    """
    if not isinstance(event, Mapping):
        return False
    row = baustelle_row(event, seen_at=when)
    if row is None or row[_GUID_COLUMN] in known:
        return False
    if _ended_before(row[_ENDS_COLUMN], when.year):
        return False
    _end_last_line(path)
    if not _append_row(path, BAUSTELLEN_HEADER, row):
        return None
    known.add(row[_GUID_COLUMN])
    return True


def append_baustellen_rows(
    events: Iterable[Mapping[str, Any]],
    *,
    now: datetime | None = None,
    stats_dir: Path | None = None,
) -> int:
    """Append one row per event whose guid the year's file does not hold yet.

    The file is the one of the Vienna year of *now* (default: the current
    moment), and *now* is the ``timestamp`` of every row written: the first
    time the site was seen in that year. Returns the number of rows written.

    A site whose last day lies before 1 January of that year gets no row
    (module docstring). A last line that a hand edit left without its line
    break is ended before the first row is written (:func:`_end_last_line`).

    Best effort like every statistics writer: it never raises. A file with a
    different header, or one that cannot be read, gets no row; an event whose
    row fails with an error is skipped and the others are still written. Each
    of these cases is logged at WARNING. An event without a usable guid or
    title (:func:`baustelle_row` gives ``None``), an entry that is no
    mapping and a site that ended before the year are passed over without a
    log line.
    """
    written = 0
    skipped = 0
    reason = ""
    try:
        when = to_vienna(now) if now is not None else datetime.now(VIENNA_TZ)
        path = stats_path("baustellen", when.year, base_dir=stats_dir)
        known = _ledger_guids(path)
        if known is None:
            return 0
        for event in events:
            try:
                outcome = _append_new(path, event, when, known)
            except Exception as exc:  # noqa: BLE001 - one site must not cost the others their row
                skipped += 1
                reason = f"{type(exc).__name__}: {exc}"
                continue
            if outcome is None:
                break
            written += 1 if outcome else 0
    except Exception as exc:  # noqa: BLE001 - statistics never stop the cache update
        LOGGER.warning(
            "Baustellen-Statistik: Zeilen konnten nicht geschrieben werden (%s: %s).",
            type(exc).__name__,
            sanitize_log_arg(str(exc)),
        )
    if skipped:
        LOGGER.warning(
            "Baustellen-Statistik: %d Baustelle(n) nicht eingetragen (%s).",
            skipped,
            sanitize_log_arg(reason),
        )
    return written


__all__ = [
    "BAUSTELLEN_HEADER",
    "MAX_BAUSTELLEN_CSV_BYTES",
    "MAX_DESCRIPTION_CHARS",
    "MAX_ROW_BYTES",
    "NO_DETAILS_TEXT",
    "LedgerLines",
    "SiteLine",
    "append_baustellen_rows",
    "baustelle_row",
    "known_guids",
    "ledger_moment",
    "read_ledger_text",
    "site_line",
    "split_ledger_line",
]
