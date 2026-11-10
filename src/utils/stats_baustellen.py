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
  sites that were under way in it.
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
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final

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
    _sanitize_csv_text_field,
)

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
_GUID_COLUMN: Final = BAUSTELLEN_HEADER.index("guid")

# The longest description measured in the 2026 history has 1,952 characters
# (median 517), so 2,000 keeps every real text whole. The other text fields
# keep the 200 characters of ``_sanitize_csv_text_field``.
MAX_DESCRIPTION_CHARS: Final = 2000
# Below ``PIPE_BUF`` (4,096 bytes), see the module docstring. 2,000 umlauts
# alone are 4,000 bytes, so the description is shortened further when the
# encoded row would reach this size.
MAX_ROW_BYTES: Final = 4000
# Read cap, same value and reason as ``MAX_STAMMSTRECKE_CSV_BYTES``: a year
# of real rows is well below 1 MiB (191 sites in 2026), and a file that was
# planted or grew by mistake must not be buffered whole.
MAX_BAUSTELLEN_CSV_BYTES: Final = 16 * 1024 * 1024

_ELLIPSIS: Final = "…"


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


def _field(value: object) -> str:
    """A short text field: one line, 200 characters, CSV formula guard."""
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    return _sanitize_csv_text_field(_one_line(value))


def _shorten(text: str, keep: int) -> str:
    """The first *keep* characters of *text*, marked as shortened."""
    return text[: max(keep, 0)].rstrip() + _ELLIPSIS


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
    return _fit_row(head, _description(event.get("description")))


def _parse_line(line: str) -> list[str] | None:
    """One physical line as CSV fields; ``None`` when it is not a CSV record."""
    try:
        return next(csv.reader(io.StringIO(line)), None)
    except csv.Error:
        return None


def _ledger_guids(path: Path) -> set[str] | None:
    """The guids in the ledger at *path*; ``None`` when it must not be extended.

    A missing or empty file is an empty ledger. ``None`` (with a WARNING)
    stands for a file that exists but cannot be read within the size cap,
    or whose first line is not :data:`BAUSTELLEN_HEADER`: rows appended to
    it would stand under the wrong columns, and with an unreadable file
    every run would append every site again. Such a file is left as it is
    until a person has looked at it.

    Every line is parsed on its own. The writer guarantees one record per
    line, so a torn or hand-edited line with an open quotation mark spoils
    only itself and not the lines after it. Only a line with the full
    column count counts: a site whose line is damaged is entered again.
    Undecodable bytes are replaced for the same reason.
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
    lines = text.split("\n")
    header = list(BAUSTELLEN_HEADER)
    if _parse_line(lines[0].rstrip("\r")) != header:
        LOGGER.warning(
            "Baustellen-Statistik: %s hat eine andere Kopfzeile als erwartet; "
            "die Datei wird nicht gelesen und nicht ergänzt.",
            sanitize_log_arg(path.name),
        )
        return None
    guids: set[str] = set()
    for line in lines[1:]:
        row = _parse_line(line.rstrip("\r"))
        # A union merge of two runs that both created the file repeats the
        # header line; it is not a site.
        if row is None or len(row) != len(header) or row == header:
            continue
        if row[_GUID_COLUMN]:
            guids.add(row[_GUID_COLUMN])
    return guids


def known_guids(year: int, *, stats_dir: Path | None = None) -> set[str]:
    """Return the guids that ``baustellen_<year>.csv`` already holds.

    Empty for a missing file and for a file that cannot be used (too large,
    unreadable, different header — logged at WARNING). Malformed lines and
    lines repeated by a union merge are tolerated, see :func:`_ledger_guids`.
    """
    guids = _ledger_guids(stats_path("baustellen", year, base_dir=stats_dir))
    return guids if guids is not None else set()


def _append_new(path: Path, event: object, when: datetime, known: set[str]) -> bool | None:
    """Append *event* unless its guid is in *known*.

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

    Best effort like every statistics writer: it never raises. A file with a
    different header, or one that cannot be read, gets no row; an event whose
    row fails with an error is skipped and the others are still written. Each
    of these cases is logged at WARNING. An event without a usable guid or
    title (:func:`baustelle_row` gives ``None``) and an entry that is no
    mapping are passed over without a log line.
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
    "append_baustellen_rows",
    "baustelle_row",
    "known_guids",
]
