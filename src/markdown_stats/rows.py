"""Data model, CSV reading and aggregation of the statistics dashboard.

Moved verbatim from ``scripts/generate_markdown_stats.py``, which
re-exports every name. The logger keeps its name
``generate_markdown_stats``.
"""
from __future__ import annotations

import csv
import io
import logging
import math
import re
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final, TypeVar
from zoneinfo import ZoneInfo

from src.feed.stammstrecke import DELAY_THRESHOLD_MINUTES
from src.utils.files import read_capped_text
from src.utils.logging import sanitize_log_arg
from src.utils.stats import (
    AUSFAELLE_HEADER,
    DEFAULT_STATS_DIR,
    STAMMSTRECKE_HEADER,
    STOERUNGEN_HEADER,
    WEEKDAY_LABELS,
    current_location_label,
    stats_path,
)


LOGGER = logging.getLogger("generate_markdown_stats")

VIENNA_TZ: Final = ZoneInfo("Europe/Vienna")

# Cap each CSV at ~25 MiB on read. At ~80 bytes per row this allows
# >300 000 rows per year — far above any realistic Stammstrecke /
# disruption log-rate. A file that exceeds the cap is treated as
# corrupted / planted and skipped; the dashboard still renders from
# whatever other inputs are available.
MAX_CSV_BYTES: Final = 25 * 1024 * 1024

# Sentinel emitted by :func:`src.utils.stats.extract_location_name` when
# no directory-anchored station can be identified for a disruption (the
# upstream title/description simply doesn't mention a station name we
# recognise). The dashboard shows no places today (the former
# "Häufigste Störungsorte" / "Hotspots" sections were removed); rows with
# this label count in the "Erfasste Störungen" total and in the weekday /
# hour distributions like every other row.
LOCATION_UNKNOWN: Final = "unbekannt"


# ---- Data classes ----------------------------------------------------------


@dataclass(frozen=True)
class StammstreckeRow:
    """One Stammstrecke per-sample mean-delay observation.

    Each row represents one cron-cycle sample for one direction —
    threshold counters at aggregation time treat the row as a single
    observation so the same physical cycle is never multiplied across
    the count.
    """

    timestamp: datetime
    weekday: str
    hour: int
    direction: str
    delay_minutes: float


@dataclass(frozen=True)
class StoerungRow:
    """One disruption first-seen observation."""

    timestamp: datetime
    weekday: str
    hour: int
    provider: str
    location_name: str


@dataclass
class StammstreckeAggregate:
    """Aggregated Stammstrecke data ready to render."""

    by_weekday_count: dict[str, int] = field(default_factory=dict)
    by_weekday_avg: dict[str, float] = field(default_factory=dict)
    by_hour_count: dict[int, int] = field(default_factory=dict)
    by_hour_avg: dict[int, float] = field(default_factory=dict)
    by_direction: dict[str, int] = field(default_factory=dict)
    total_observations: int = 0
    threshold_exceedances: int = 0
    threshold_minutes: float = DELAY_THRESHOLD_MINUTES


@dataclass
class StoerungAggregate:
    """Aggregated Störungen data ready to render."""

    by_weekday: dict[str, int] = field(default_factory=dict)
    by_hour: dict[int, int] = field(default_factory=dict)
    by_provider: dict[str, int] = field(default_factory=dict)
    total_disruptions: int = 0


@dataclass(frozen=True)
class AusfallRow:
    """One Stammstrecke cancellation observation.

    Each row represents exactly one cancelled S-Bahn train (deduped
    upstream by the pending-trip ledger's identity-key machinery — the
    same train re-observed across multiple cron ticks contributes one
    row regardless of how many ticks saw it). The dashboard counts
    rows directly without further aggregation; per-direction and
    per-line breakdowns surface the operational signal ("which line is
    cancelling most often?").
    """

    timestamp: datetime
    weekday: str
    hour: int
    direction: str
    line: str


@dataclass
class AusfallAggregate:
    """Aggregated Ausfälle data ready to render."""

    by_weekday: dict[str, int] = field(default_factory=dict)
    by_hour: dict[int, int] = field(default_factory=dict)
    by_direction: dict[str, int] = field(default_factory=dict)
    by_line: dict[str, int] = field(default_factory=dict)
    total_cancellations: int = 0


# ---- CSV reading -----------------------------------------------------------


def _iter_csv_rows(path: Path, header: tuple[str, ...]) -> Iterator[dict[str, str]]:
    """Yield rows from *path* as dicts, validating the header.

    Routes the read through :func:`src.utils.files.read_capped_text`
    (open + ``fstat`` + capped ``read``) and constructs
    :class:`csv.reader` from an in-memory :class:`io.StringIO`. This
    matches the project-wide drift-defence sentinel against unbounded
    CSV reads — never hand a raw file handle to the csv module.
    """
    raw = read_capped_text(
        path,
        max_bytes=MAX_CSV_BYTES,
        label="stats CSV",
        logger=LOGGER,
    )
    if raw is None:
        return
    reader = csv.reader(io.StringIO(raw))
    try:
        actual_header = next(reader)
    except StopIteration:
        return
    if tuple(actual_header) != header:
        LOGGER.warning(
            "Stats-Datei %s hat unerwarteten Header %r — überspringe.",
            sanitize_log_arg(str(path)),
            sanitize_log_arg(str(actual_header)),
        )
        return
    for row in reader:
        if len(row) != len(header):
            continue
        yield dict(zip(header, row, strict=True))


def _aware_fromisoformat(value: str) -> datetime:
    """Parse an ISO-8601 timestamp, coercing a naive result to Vienna time.

    The CSV writer emits Vienna-aware timestamps, but a hand-edited row (or a
    future writer that drops the offset) yields a *naive* ``datetime`` from
    ``fromisoformat``. Comparing that against the tz-aware ``cutoff`` in
    ``_filter_rows_by_window`` / ``_filter_rows_by_timedelta`` raises
    ``TypeError: can't compare offset-naive and offset-aware datetimes`` —
    which no ``except`` in ``main`` catches, so ``docs/statistik.md`` is written
    but the README patch crashes (exit 1) and the README is left stale. Treating
    a naive value as Europe/Vienna mirrors the ``--now-iso`` handling
    (``now = now.replace(tzinfo=VIENNA_TZ)``) and the CSV writer's own
    ``to_vienna`` convention, so genuinely-offset rows are unaffected.
    """
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=VIENNA_TZ)
    return parsed


def _parse_stammstrecke_rows(
    raw_rows: Iterable[dict[str, str]],
) -> list[StammstreckeRow]:
    """Convert raw CSV dict rows to typed Stammstrecke records.

    Malformed rows (unparseable timestamp, non-numeric delay) are
    dropped silently — the aggregator's only contract is that the
    output reflects the *parseable* data. A single bad row, possibly
    introduced by hand-editing, must never poison the whole dashboard.
    """
    parsed: list[StammstreckeRow] = []
    for row in raw_rows:
        try:
            ts = _aware_fromisoformat(row["timestamp"])
        except (KeyError, ValueError, TypeError):
            continue
        try:
            delay = float(row["delay_minutes"])
        except (KeyError, ValueError, TypeError):
            continue
        # NaN/Inf must never reach the renderer: ``_scale_bar`` bypasses its
        # ``value <= 0`` guard for NaN (IEEE 754) and then ``int(round(nan))``
        # raises ValueError, crashing dashboard generation and leaving
        # docs/statistik.md stale. Drop the corrupt row instead.
        if not math.isfinite(delay):
            continue
        weekday = row.get("weekday") or WEEKDAY_LABELS[ts.weekday()]
        try:
            hour = int(row.get("hour") or ts.hour)
        except ValueError:
            hour = ts.hour
        direction = (row.get("direction") or "Unbekannt").strip() or "Unbekannt"
        parsed.append(
            StammstreckeRow(
                timestamp=ts,
                weekday=weekday,
                hour=max(0, min(23, hour)),
                direction=direction,
                delay_minutes=delay,
            )
        )
    return parsed


def _parse_stoerung_rows(
    raw_rows: Iterable[dict[str, str]],
) -> list[StoerungRow]:
    """Convert raw CSV dict rows to typed Störung records."""
    parsed: list[StoerungRow] = []
    for row in raw_rows:
        try:
            ts = _aware_fromisoformat(row["timestamp"])
        except (KeyError, ValueError, TypeError):
            continue
        weekday = row.get("weekday") or WEEKDAY_LABELS[ts.weekday()]
        try:
            hour = int(row.get("hour") or ts.hour)
        except ValueError:
            hour = ts.hour
        provider = (row.get("provider") or "unbekannt").strip() or "unbekannt"
        location = (row.get("location_name") or "unbekannt").strip() or "unbekannt"
        # Older rows carry the display name of their day ("St. Pölten
        # Hauptbahnhof" before 2026-11-02); one place keeps one label.
        # No section renders the place today, so the output is unchanged.
        location = current_location_label(location)
        parsed.append(
            StoerungRow(
                timestamp=ts,
                weekday=weekday,
                hour=max(0, min(23, hour)),
                provider=provider,
                location_name=location,
            )
        )
    return parsed


def _parse_ausfall_rows(
    raw_rows: Iterable[dict[str, str]],
) -> list[AusfallRow]:
    """Convert raw CSV dict rows to typed Ausfall records.

    Same corruption-tolerance contract as the other parsers: a row
    with an unparseable timestamp or missing required field is silently
    dropped (a single hand-edited bad row must never break the
    dashboard regeneration).
    """
    parsed: list[AusfallRow] = []
    for row in raw_rows:
        try:
            ts = _aware_fromisoformat(row["timestamp"])
        except (KeyError, ValueError, TypeError):
            continue
        weekday = row.get("weekday") or WEEKDAY_LABELS[ts.weekday()]
        try:
            hour = int(row.get("hour") or ts.hour)
        except ValueError:
            hour = ts.hour
        direction = (row.get("direction") or "unbekannt").strip() or "unbekannt"
        line = (row.get("line") or "unbekannt").strip() or "unbekannt"
        parsed.append(
            AusfallRow(
                timestamp=ts,
                weekday=weekday,
                hour=max(0, min(23, hour)),
                direction=direction,
                line=line,
            )
        )
    return parsed


# ---- Aggregation -----------------------------------------------------------


def aggregate_stammstrecke(
    rows: list[StammstreckeRow],
    *,
    threshold_minutes: float = DELAY_THRESHOLD_MINUTES,
) -> StammstreckeAggregate:
    """Roll *rows* up into the dimensions the dashboard needs.

    *threshold_minutes* mirrors :data:`scripts.update_stammstrecke_status.
    DELAY_THRESHOLD_MINUTES`. ``threshold_exceedances`` counts each
    *row* whose persisted per-sample mean delay strictly exceeds the
    threshold — never more than once per cron cycle, so the same
    physical cycle is not multiplied into the count.
    """
    weekday_count: dict[str, int] = defaultdict(int)
    weekday_sum: dict[str, float] = defaultdict(float)
    hour_count: dict[int, int] = defaultdict(int)
    hour_sum: dict[int, float] = defaultdict(float)
    direction_count: dict[str, int] = defaultdict(int)
    exceedances = 0

    for row in rows:
        weekday_count[row.weekday] += 1
        weekday_sum[row.weekday] += row.delay_minutes
        hour_count[row.hour] += 1
        hour_sum[row.hour] += row.delay_minutes
        direction_count[row.direction] += 1
        if row.delay_minutes > threshold_minutes:
            exceedances += 1

    weekday_avg = {
        wd: weekday_sum[wd] / weekday_count[wd]
        for wd in weekday_count
    }
    hour_avg = {h: hour_sum[h] / hour_count[h] for h in hour_count}

    return StammstreckeAggregate(
        by_weekday_count=dict(weekday_count),
        by_weekday_avg=weekday_avg,
        by_hour_count=dict(hour_count),
        by_hour_avg=hour_avg,
        by_direction=dict(direction_count),
        total_observations=len(rows),
        threshold_exceedances=exceedances,
        threshold_minutes=threshold_minutes,
    )


def aggregate_stoerungen(rows: list[StoerungRow]) -> StoerungAggregate:
    """Roll *rows* up into the dimensions the dashboard needs."""
    weekday_count: dict[str, int] = defaultdict(int)
    hour_count: dict[int, int] = defaultdict(int)
    provider_count: dict[str, int] = defaultdict(int)

    for row in rows:
        weekday_count[row.weekday] += 1
        hour_count[row.hour] += 1
        provider_count[row.provider] += 1

    return StoerungAggregate(
        by_weekday=dict(weekday_count),
        by_hour=dict(hour_count),
        by_provider=dict(provider_count),
        total_disruptions=len(rows),
    )


# A cancelled train whose VAO name is its train number, not a line: "S28700",
# "CJX2932". Lines on the Stammstrecke have at most three digits (S45, R81,
# REX92); ÖBB train numbers have four or five. Between 27.05. and 07.07.2026
# eleven cancellation rows carried such a name. Nine were the twin of a line
# entry VAO listed at the same minute in the same direction, the same train
# (``_without_train_number_twins`` in scripts/update_stammstrecke_hbf.py), and
# left the ledger on 26.10.2026; the other two got their line from the
# timetable (the line leaving Wien Hbf in the same direction at the same
# minute on the other days; Michael's yes 26.10.2026 00:23 Vienna). A train
# number counts under this label instead of as a line of its own; the ledger
# keeps the name VAO gave.
NO_LINE_LABEL: Final = "ohne Linienangabe"
_TRAIN_NUMBER_RE: Final = re.compile(r"^(?:S|REX|R|CJX)\d{4,}$")


def ausfall_line_label(line: str) -> str:
    """The line a cancellation counts under: ``line``, or :data:`NO_LINE_LABEL`."""
    return NO_LINE_LABEL if _TRAIN_NUMBER_RE.match(line.strip()) else line


def aggregate_ausfaelle(rows: list[AusfallRow]) -> AusfallAggregate:
    """Roll cancellation rows up into the dimensions the dashboard needs."""
    weekday_count: dict[str, int] = defaultdict(int)
    hour_count: dict[int, int] = defaultdict(int)
    direction_count: dict[str, int] = defaultdict(int)
    line_count: dict[str, int] = defaultdict(int)

    for row in rows:
        weekday_count[row.weekday] += 1
        hour_count[row.hour] += 1
        direction_count[row.direction] += 1
        line_count[ausfall_line_label(row.line)] += 1

    return AusfallAggregate(
        by_weekday=dict(weekday_count),
        by_hour=dict(hour_count),
        by_direction=dict(direction_count),
        by_line=dict(line_count),
        total_cancellations=len(rows),
    )


# ---- README snapshot rendering --------------------------------------------


_TimestampedRow = TypeVar(
    "_TimestampedRow", StammstreckeRow, StoerungRow, AusfallRow
)


def _filter_rows_by_window(
    rows: Iterable[_TimestampedRow],
    *,
    days: int,
    now: datetime,
) -> list[_TimestampedRow]:
    """Return the subset of *rows* whose ``timestamp`` is in the last *days*.

    The cutoff is *now − days* (inclusive lower bound). Both the row
    timestamps and *now* MUST carry tzinfo (they do — the CSV writer
    normalises to ``Europe/Vienna`` and the orchestrator constructs
    *now* via :func:`datetime.now` with :data:`VIENNA_TZ`).
    """
    if days <= 0:
        return []
    cutoff = now - timedelta(days=days)
    return [r for r in rows if r.timestamp >= cutoff]


def _filter_rows_by_timedelta(
    rows: Iterable[_TimestampedRow],
    *,
    delta: timedelta,
    now: datetime,
) -> list[_TimestampedRow]:
    """Return the subset of *rows* whose ``timestamp`` is in the last *delta*.
    """
    if delta.total_seconds() <= 0:
        return []
    cutoff = now - delta
    return [r for r in rows if r.timestamp >= cutoff]


# ---- Orchestration ---------------------------------------------------------


def collect_year_data(
    year: int,
    *,
    stats_dir: Path | None = None,
) -> tuple[list[StammstreckeRow], list[StoerungRow], list[AusfallRow]]:
    """Load and parse all stats CSVs for *year*.

    The CSVs are read independently — a missing or malformed file
    does not prevent the others from loading. A repo that has never
    seen a cancellation produces an empty ``ausfaelle_<YYYY>.csv``
    (or no file at all); the parser returns ``[]`` in both shapes so
    the dashboard renders a clean "_Keine Ausfälle erfasst._" row
    instead of failing.
    """
    base = stats_dir if stats_dir is not None else DEFAULT_STATS_DIR
    sm_path = stats_path("stammstrecke", year, base_dir=base)
    st_path = stats_path("stoerungen", year, base_dir=base)
    au_path = stats_path("ausfaelle", year, base_dir=base)
    sm_rows = _parse_stammstrecke_rows(_iter_csv_rows(sm_path, STAMMSTRECKE_HEADER))
    st_rows = _parse_stoerung_rows(_iter_csv_rows(st_path, STOERUNGEN_HEADER))
    au_rows = _parse_ausfall_rows(_iter_csv_rows(au_path, AUSFAELLE_HEADER))
    return sm_rows, st_rows, au_rows


def earlier_rows_until_every_direction(
    rows: Iterable[StammstreckeRow],
    *,
    before_year: int,
    directions: tuple[str, ...],
    stats_dir: Path | None = None,
) -> list[StammstreckeRow]:
    """Delay rows of the years before *before_year*, back to each direction's last one.

    The coverage note and the website banner say when a silent direction
    last reported and how many journeys the other one has logged since
    (``find_silent_coverage_directions``). They read that off the rows they
    are given, and the run loaded the year files its 30-day window touches:
    the current year and, until 30.01., the one before. A direction whose
    last journey lies in a year file that is no longer loaded then counted
    as "never measured", and README and website dropped the caveat while the
    direction was as silent as the day before (found 2026-11-10 by replaying
    the real ledger past New Year: Praterstern, silent since 14.08.2026,
    lost its note on 31.01.2027).

    So when *rows* lack a direction, the earlier year files are read, newest
    first, until every direction has a row or a year has no file. With
    every direction present nothing is read. While a direction has been
    silent since before the loaded year files, every run reads the earlier
    delay ledger again: with the real ledger (Praterstern silent since
    14.08.2026) that is every tick from 31.01.2027, about 65 ms for the
    2026 file (review of 2026-11-10). A state file with the last row of each
    direction would save it, but it would be a second record beside the
    ledger, which the pages follow (Michael, 2026-11-10 19:13).
    """
    base = stats_dir if stats_dir is not None else DEFAULT_STATS_DIR
    seen = {row.direction for row in rows}
    earlier: list[StammstreckeRow] = []
    year = before_year - 1
    while any(d not in seen for d in directions):
        path = stats_path("stammstrecke", year, base_dir=base)
        if not path.is_file():
            break
        year_rows = _parse_stammstrecke_rows(_iter_csv_rows(path, STAMMSTRECKE_HEADER))
        earlier.extend(year_rows)
        seen.update(row.direction for row in year_rows)
        year -= 1
    return earlier
