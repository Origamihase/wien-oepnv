"""Append-only CSV statistics writers.

The two writers exposed by this module — :func:`append_stammstrecke_row`
and :func:`append_disruption_row` — both follow the same contract:

* The target file lives under ``<repo>/data/stats/`` and is named
  ``<kind>_YYYY.csv`` with the year derived from the supplied
  ``timestamp`` (Europe/Vienna). One file per calendar year keeps
  individual files small even after many years of operation.
* The file is opened in **append mode** (``"a"``). On POSIX, a single
  ``write()`` of a row below ``PIPE_BUF`` (4 KiB by default — our rows
  are well below that) is atomic against concurrent appenders, so we do
  not need a lock for typical operation.
* The CSV header is written exactly once, when the file does not yet
  exist. Subsequent appends only add data rows.
* All failures (full disk, permissions, OS-level write errors, …) are
  swallowed at WARNING level so the calling pipeline (cron job,
  build_feed.main) never crashes because statistics could not be
  recorded — statistics are observability, not core functionality.

The module is intentionally dependency-free: only standard library
imports plus :func:`src.utils.logging.sanitize_log_arg` for the
WARNING-level diagnostics. Mypy-strict clean; no third-party calls.
"""
from __future__ import annotations

import csv
import io
import logging
import math
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

from src.utils.files import read_capped_text
from src.utils.logging import sanitize_log_arg

# The CSV sanitiser and the location extraction live in sibling modules
# (700-line limit, 2026-11-02); every name stays reachable here.
from src.utils.stats_sanitize import (
    _CSV_CONTROL_CHARS_RE as _CSV_CONTROL_CHARS_RE,
    _CSV_FORMULA_PREFIXES as _CSV_FORMULA_PREFIXES,
    _CSV_TEXT_FIELD_MAX_LEN as _CSV_TEXT_FIELD_MAX_LEN,
    _sanitize_csv_text_field as _sanitize_csv_text_field,
)
from src.utils.stats_location import (
    MAX_LABELLED_SCAN_CHARS as MAX_LABELLED_SCAN_CHARS,
    _BETWEEN_RE as _BETWEEN_RE,
    _GENERIC_DIRECTORY_TOKENS as _GENERIC_DIRECTORY_TOKENS,
    _HALTESTELLE_RE as _HALTESTELLE_RE,
    _LABELED_LOCATION_RE as _LABELED_LOCATION_RE,
    _MAX_STATION_WINDOW as _MAX_STATION_WINDOW,
    _NOISE_TOKEN_RE as _NOISE_TOKEN_RE,
    _RICHTUNG_RE as _RICHTUNG_RE,
    _TOKEN_SPLIT_RE as _TOKEN_SPLIT_RE,
    _WIEN_PREFIX_RE as _WIEN_PREFIX_RE,
    _normalise_location as _normalise_location,
    _resolve_via_directory as _resolve_via_directory,
    _scan_for_directory_station as _scan_for_directory_station,
    current_location_label as current_location_label,
    extract_location_name as extract_location_name,
)

LOGGER = logging.getLogger("utils.stats")

VIENNA_TZ: Final = ZoneInfo("Europe/Vienna")

# Repository-root-relative default location. Resolved lazily so tests
# that monkeypatch the constant pick up the override.
DEFAULT_STATS_DIR: Final = (
    Path(__file__).resolve().parents[2] / "data" / "stats"
)

# Header rows. Pinned constants so the aggregator and the writers cannot
# drift apart silently — a header rename here breaks the aggregator at
# import time, which is the desired loud-failure mode.
STAMMSTRECKE_HEADER: Final = (
    "timestamp",
    "weekday",
    "hour",
    "direction",
    "delay_minutes",
)
STOERUNGEN_HEADER: Final = (
    "timestamp",
    "weekday",
    "hour",
    "provider",
    "location_name",
)
AUSFAELLE_HEADER: Final = (
    "timestamp",
    "weekday",
    "hour",
    "direction",
    "line",
)

# German short weekday names (Mo, Di, Mi, …) keyed by ``datetime.weekday()``
# (0 = Monday). The aggregator renders these verbatim, so any change here
# also changes the dashboard labels.
WEEKDAY_LABELS: Final = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")

def to_vienna(dt: datetime) -> datetime:
    """Return *dt* localised to ``Europe/Vienna``.

    A naive datetime is assumed to already be in Vienna time (we control
    the call sites; the only naive input would be a programming error
    in a fresh provider). A timezone-aware datetime is converted via
    :meth:`datetime.astimezone`.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=VIENNA_TZ)
    return dt.astimezone(VIENNA_TZ)


def stats_path(kind: str, year: int, *, base_dir: Path | None = None) -> Path:
    """Return the path to ``data/stats/<kind>_YYYY.csv``.

    Uses :data:`DEFAULT_STATS_DIR` unless *base_dir* is supplied. Tests
    monkeypatch ``base_dir`` via the writer-level ``stats_dir`` keyword
    so the production constant stays read-only.
    """
    folder = base_dir if base_dir is not None else DEFAULT_STATS_DIR
    return folder / f"{kind}_{year:04d}.csv"


class HeldRows:
    """The rows a :func:`rows_held_back` block kept back, in their order."""

    def __init__(self) -> None:
        self._rows: list[tuple[Path, tuple[str, ...], tuple[str, ...]]] = []

    def __len__(self) -> int:
        return len(self._rows)

    def write(self) -> None:
        """Append the kept rows to their files now; each row is written once.

        Best-effort like every writer of this module: a row that cannot be
        written is logged and the next one is tried.
        """
        rows, self._rows = self._rows, []
        for path, header, row in rows:
            try:
                _append_row(path, header, row)
            except Exception as exc:  # noqa: BLE001 - statistics never stop the build
                LOGGER.warning(
                    "Stats append fehlgeschlagen für %s: %s",
                    sanitize_log_arg(str(path)),
                    sanitize_log_arg(str(exc)),
                )


_HELD_ROWS: ContextVar[HeldRows | None] = ContextVar("stats_held_rows", default=None)


@contextmanager
def rows_held_back() -> Iterator[HeldRows]:
    """Keep the rows appended inside the block back instead of writing them.

    The feed build renders an item and enters its first appearance into the
    ledger in one step. Since 2026-11-10 a rendering can be a trial or can
    be repeated without one item (``src/feed_build/isolation.py``); only
    the rendering whose feed the build goes on with may count. The caller
    writes the kept rows with :meth:`HeldRows.write` once that rendering has
    succeeded (before the feed file is written); rows that are not written
    are dropped.
    """
    held = HeldRows()
    token = _HELD_ROWS.set(held)
    try:
        yield held
    finally:
        _HELD_ROWS.reset(token)


def _append_row(
    path: Path,
    header: tuple[str, ...],
    row: tuple[str, ...],
) -> bool:
    """Append *row* to *path*, writing the header on first creation.

    Returns ``True`` on success, ``False`` if any I/O error was caught
    (and logged at WARNING). The boolean return makes the writers
    individually testable without scraping log output. Inside a
    :func:`rows_held_back` block the row is only kept (``True``) and
    written by :meth:`HeldRows.write`.

    Implementation note: ``newline=""`` is the canonical way to disable
    Python's universal-newline translation on writes through
    :mod:`csv`, which would otherwise add a stray ``\\r`` on Windows
    and break round-tripping.
    """
    held = _HELD_ROWS.get()
    if held is not None:
        held._rows.append((path, header, row))
        return True
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_header = not path.exists() or path.stat().st_size == 0
        with path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            if write_header:
                writer.writerow(header)
            writer.writerow(row)
        return True
    except OSError as exc:
        LOGGER.warning(
            "Stats append fehlgeschlagen für %s: %s",
            sanitize_log_arg(str(path)),
            sanitize_log_arg(str(exc)),
        )
        return False


def _format_delay(delay_minutes: float) -> str:
    """Return a stable two-decimal string representation of *delay_minutes*.

    Two decimals are enough to preserve the per-second resolution that
    HAFAS sometimes reports while keeping CSV rows compact. ``round``
    avoids platform-specific float rendering ('11.099999999999998').
    """
    return f"{round(float(delay_minutes), 2):.2f}"


def append_stammstrecke_row(
    *,
    timestamp: datetime,
    direction: str,
    delay_minutes: float,
    stats_dir: Path | None = None,
) -> bool:
    """Append a single observation to ``stammstrecke_YYYY.csv``.

    *direction* is the human-readable target station ("Meidling",
    "Floridsdorf"). *delay_minutes* is the arithmetic mean of the
    departure delays observed across the sampled S-Bahn legs for
    that direction (legs without realtime signal are excluded
    upstream so the mean reflects only verified observations).
    Each row represents one cron-cycle sample for one direction —
    threshold counters at aggregation time treat every such row as a
    single observation, never multiplying it by the number of legs
    within the sample.

    The function is best-effort: filesystem errors are logged and
    swallowed so the upstream cron pipeline always exits cleanly.
    """
    # Defense-in-depth (non-finite floor): a non-finite delay would be
    # rendered as the literal "nan"/"inf" into the ledger, which the
    # reader's ``float(raw_delay)`` re-accepts — silently poisoning every
    # downstream mean/threshold aggregation. Upstream producers guard
    # against this today, but the project pins ``allow_nan=False`` on every
    # JSON sink for exactly this reason; mirror that floor here at the CSV
    # write boundary. Skip the row (best-effort contract: log + return
    # False, never raise) rather than write a corrupt value.
    if not math.isfinite(delay_minutes):
        LOGGER.warning(
            "Nicht-finiter delay_minutes (%r) – Stammstrecke-Zeile übersprungen.",
            delay_minutes,
        )
        return False
    when = to_vienna(timestamp)
    path = stats_path("stammstrecke", when.year, base_dir=stats_dir)
    row = (
        when.isoformat(timespec="seconds"),
        WEEKDAY_LABELS[when.weekday()],
        f"{when.hour:02d}",
        _sanitize_csv_text_field(direction),
        _format_delay(delay_minutes),
    )
    return _append_row(path, STAMMSTRECKE_HEADER, row)


def append_disruption_row(
    *,
    timestamp: datetime,
    provider: str,
    location_name: str,
    stats_dir: Path | None = None,
) -> bool:
    """Append a single freshly-detected disruption to ``stoerungen_YYYY.csv``.

    The caller is responsible for ensuring that *timestamp* corresponds
    to the moment the event was first observed (``first_seen``), not
    some derivative timestamp from the event payload itself — this
    keeps the dashboard's time-of-day binning aligned with operator
    observation times rather than upstream-supplied event metadata.

    Best-effort; never raises.
    """
    when = to_vienna(timestamp)
    path = stats_path("stoerungen", when.year, base_dir=stats_dir)
    row = (
        when.isoformat(timespec="seconds"),
        WEEKDAY_LABELS[when.weekday()],
        f"{when.hour:02d}",
        _sanitize_csv_text_field(provider) or "unbekannt",
        _sanitize_csv_text_field(location_name) or "unbekannt",
    )
    return _append_row(path, STOERUNGEN_HEADER, row)


def append_ausfall_row(
    *,
    timestamp: datetime,
    direction: str,
    line: str,
    stats_dir: Path | None = None,
) -> bool:
    """Append a single Stammstrecke cancellation to ``ausfaelle_YYYY.csv``.

    Each row represents exactly one cancelled train (deduplicated upstream
    by the pending-trip ledger's identity-key machinery so the same
    physical cancelled train is never written twice across cron ticks).
    *timestamp* is the train's *scheduled* departure time — anchoring the
    row to the actual departure window rather than to the cron wall clock
    keeps the calendar-year ledger correct at the New-Year boundary.

    *direction* is the canonical Stammstrecke direction label
    (``Meidling`` / ``Praterstern``); *line* is the canonicalised line
    designation (``S1``, ``REX3``, …). Both fields flow through
    :func:`_sanitize_csv_text_field` so a poisoned upstream payload
    cannot inject a spreadsheet formula or invisible-format characters
    into the committed CSV.

    Best-effort; never raises.
    """
    when = to_vienna(timestamp)
    path = stats_path("ausfaelle", when.year, base_dir=stats_dir)
    row = (
        when.isoformat(timespec="seconds"),
        WEEKDAY_LABELS[when.weekday()],
        f"{when.hour:02d}",
        _sanitize_csv_text_field(direction) or "unbekannt",
        _sanitize_csv_text_field(line) or "unbekannt",
    )
    return _append_row(path, AUSFAELLE_HEADER, row)


# Hard byte cap on the per-year ``stammstrecke_<YYYY>.csv`` file before
# the feed-side reader will accept it. The append-only writer produces
# ~50-byte rows and runs every 30 minutes (≈876 KiB/year worst case).
# 16 MiB is ~18× the worst-case annual footprint while bounding the
# memory cost of an adversarial planted file (compromised CI runner /
# operator mis-edit / partial-flush + power-loss). Mirrors the
# defense-in-depth size cap on every other JSON cache reader in the
# project.
MAX_STAMMSTRECKE_CSV_BYTES: Final = 16 * 1024 * 1024


@dataclass(frozen=True)
class StammstreckeObservation:
    """A single ``stammstrecke_*.csv`` row, parsed and validated.

    Field shapes mirror :data:`STAMMSTRECKE_HEADER`. The reader below
    silently drops rows that fail to parse so a single bad row cannot
    break the entire feed-build (which then would have to operate
    without the Stammstrecke event entirely).
    """

    timestamp: datetime
    direction: str
    delay_minutes: float


def _parse_stammstrecke_row(row: dict[str, str]) -> StammstreckeObservation | None:
    """Best-effort parser for a CSV row dict — returns ``None`` on shape errors.

    The same row schema the writer guarantees (``timestamp,weekday,hour,
    direction,delay_minutes``); we only re-validate the fields the
    reader actually consumes (timestamp + direction + delay_minutes).
    """
    raw_ts = (row.get("timestamp") or "").strip()
    raw_dir = (row.get("direction") or "").strip()
    raw_delay = (row.get("delay_minutes") or "").strip()
    if not raw_ts or not raw_dir or not raw_delay:
        return None
    try:
        ts = datetime.fromisoformat(raw_ts)
    except ValueError:
        return None
    if ts.tzinfo is None:
        # Naive timestamps in the ledger are unexpected — the writer
        # always emits an offset-aware ISO string. Treat them as
        # Europe/Vienna defensively rather than dropping the row.
        ts = ts.replace(tzinfo=VIENNA_TZ)
    try:
        delay = float(raw_delay)
    except ValueError:
        return None
    if not math.isfinite(delay):
        # Reader-side non-finite floor — symmetric with the writer guard in
        # ``append_stammstrecke_row``. ``float("nan")`` / ``float("inf")`` /
        # ``float("1e400")`` parse without raising and would otherwise be
        # re-accepted verbatim, silently poisoning every downstream
        # mean/threshold aggregation (the exact threat the writer docstring
        # warns about). Drop the row rather than propagate a poison value.
        return None
    return StammstreckeObservation(
        timestamp=ts, direction=raw_dir, delay_minutes=delay
    )


def read_recent_stammstrecke_observations(
    *,
    now: datetime,
    window: timedelta,
    stats_dir: Path | None = None,
) -> list[StammstreckeObservation]:
    """Return all Stammstrecke observations whose timestamp is in the last *window*.

    Reads the per-year CSV files spanning the requested window (a window
    that crosses a year boundary triggers two file reads); rows that
    fail to parse are silently dropped at WARNING. The result is sorted
    by timestamp ascending so callers can fold over it deterministically.

    Best-effort: every I/O / parse failure is caught and logged; the
    function returns whatever rows it could read, which means an empty
    list when the ledger is missing or unreadable. The caller treats
    "no observations" as "no event" — the feed naturally degrades to
    omitting the Stammstrecke entry rather than failing the build.

    Defense-in-depth: every CSV file is size-capped at
    :data:`MAX_STAMMSTRECKE_CSV_BYTES` before opening.
    """
    if window.total_seconds() <= 0:
        return []
    # Localise ``now`` so a naive argument can't raise ``TypeError`` when compared
    # against the always-aware ``parsed.timestamp`` below — the docstring promises
    # this function never raises and degrades to "no observations" instead.
    now = to_vienna(now)
    # Subtract in UTC: ``now - window`` on a Vienna datetime moves the wall
    # clock, so across a clock change the window would be an hour too long
    # or, on the last Sunday in March, empty from 03:00 to 04:00.
    cutoff = now.astimezone(UTC) - window
    folder = stats_dir if stats_dir is not None else DEFAULT_STATS_DIR
    # Read every calendar year the window spans, not just its two
    # boundaries: a window wider than one full year (no current caller
    # passes one, but the API is public) would otherwise silently skip the
    # intermediate years' ledgers. ``range`` is identical to the former
    # ``{cutoff.year, now.year}`` set for the ≤1-year windows used today.
    # Year-file names are derived from the *Vienna-local* year at write time
    # (:func:`append_stammstrecke_row` uses ``to_vienna(timestamp).year``), so
    # the file-selection range must localise too. A UTC-aware ``now`` in the
    # first hour(s) after a Vienna New-Year would otherwise scan only the
    # previous year's ledger and miss rows just written to the new one.
    years = list(range(to_vienna(cutoff).year, to_vienna(now).year + 1))
    observations: list[StammstreckeObservation] = []
    for year in years:
        path = folder / f"stammstrecke_{year:04d}.csv"
        # ``read_capped_text`` enforces the size cap before buffering
        # the whole file into memory and returns ``None`` on missing /
        # oversized / unreadable files (incl. ``MemoryError`` defence
        # via TOCTOU-safe fstat). The ``io.StringIO`` round-trip then
        # feeds the bounded text into the standard csv module — wrap
        # mirrors the project-wide pattern enforced by
        # ``tests/test_sentinel_csv_size_bomb.py``.
        text = read_capped_text(
            path,
            MAX_STAMMSTRECKE_CSV_BYTES,
            label="Stammstrecke ledger",
            logger=LOGGER,
        )
        if text is None:
            continue
        reader = csv.DictReader(io.StringIO(text))
        try:
            for row in reader:
                parsed = _parse_stammstrecke_row(row)
                if parsed is None:
                    continue
                if parsed.timestamp < cutoff:
                    continue
                observations.append(parsed)
        except csv.Error as exc:
            # ``csv`` can raise (e.g. a quoted field exceeding
            # ``csv.field_size_limit`` still fits under the byte cap) only
            # while iterating — after ``read_capped_text`` already returned.
            # Honour the documented best-effort contract: keep the rows read
            # so far (incl. earlier year-files) instead of propagating.
            LOGGER.warning(
                "Stammstrecke ledger %s konnte nicht vollständig gelesen werden: %s",
                sanitize_log_arg(str(path)),
                sanitize_log_arg(str(exc)),
            )
            continue
    observations.sort(key=lambda obs: obs.timestamp)
    return observations


# --- direction-silence detection (audit B.1 / B.2) ---------------------------
#
# The Stammstrecke monitor writes one delay row per direction per tick. On
# 2026-08-14 the northbound direction stopped producing rows entirely (a real
# closure: construction plus the cable-fire aftermath diverted those services
# off the two trunk platforms at Wien Hbf). Nothing noticed for 30 days, and the
# published 30-day figures kept presenting a single-direction sample as a
# whole-corridor one. These helpers are the detector that was missing.
#
# Why "no rows at all in the window" is NOT the rule: the corridor legitimately
# goes quiet at night, so an absolute threshold has to be set above the longest
# normal gap. Measured over the healthy period (2026-05-17 .. 08-13, 7589 rows):
# median gap 0.5 h, p99 3.5-3.7 h, but the observed maxima are 7.8 h (Meidling)
# and 11.5 h (Praterstern). A 6 h absolute rule would therefore have fired 5
# false alarms; only a >= 12 h threshold is quiet, and that halves the detection
# speed.
#
# The rule below compares the directions against each other instead: a direction
# counts as silent when it has NO rows while a peer direction is demonstrably
# active in the same window. Both directions going quiet together (night, a full
# upstream outage, a dead cron) is not a direction fault and is left to the
# feed-freshness and updater checks. Replayed over the same healthy period at a
# 6 h window this rule produces ZERO false alarms across 2113 hourly samples,
# while still flagging the 2026-08-14 outage at every one of the 692 samples
# after it began — first alarm 6 h after the last northbound row.
#: Canonical Stammstrecke direction labels, in report order.
#:
#: Mirrors ``scripts.update_stammstrecke_hbf.DIRECTION_LABELS``; that module is
#: the producer, but importing it here would drag ``requests`` into this
#: stdlib-only module. ``tests/test_stammstrecke_direction_health/`` pins the
#: two tuples together so they cannot drift.
STAMMSTRECKE_DIRECTIONS: Final = ("Meidling", "Praterstern")

DIRECTION_SILENCE_WINDOW_HOURS: Final = 6.0
#: Rows a peer direction must show before a silent direction is called a fault.
#: Guards the boundary case where the corridor is only just waking up and the
#: peer happens to have exactly one row.
DIRECTION_SILENCE_MIN_PEER_ROWS: Final = 3


@dataclass(frozen=True)
class DirectionActivity:
    """Row count for one direction inside the inspected window."""

    direction: str
    rows: int


def summarise_direction_activity(
    *,
    directions: tuple[str, ...],
    now: datetime,
    window: timedelta | None = None,
    stats_dir: Path | None = None,
) -> list[DirectionActivity]:
    """Count recent delay-ledger rows per direction.

    Reads the *delay* ledger only. The cancellation ledger is deliberately not
    consulted: it records rows only when trains are actually cancelled, so a
    direction with no cancellations is normal and would make any staleness rule
    fire constantly.

    Best-effort, like the reader it builds on — an unreadable ledger yields zero
    counts rather than raising, and the caller degrades to "cannot tell".
    """
    span = window if window is not None else timedelta(hours=DIRECTION_SILENCE_WINDOW_HOURS)
    observations = read_recent_stammstrecke_observations(
        now=now, window=span, stats_dir=stats_dir
    )
    counts = dict.fromkeys(directions, 0)
    for obs in observations:
        if obs.direction in counts:
            counts[obs.direction] += 1
    return [DirectionActivity(direction=d, rows=counts[d]) for d in directions]


def find_silent_directions(
    *,
    directions: tuple[str, ...],
    now: datetime,
    window: timedelta | None = None,
    min_peer_rows: int = DIRECTION_SILENCE_MIN_PEER_ROWS,
    stats_dir: Path | None = None,
) -> list[str]:
    """Return the directions that are silent while a peer direction is active.

    A direction is *silent* when it contributed no rows to the window and some
    other direction contributed at least *min_peer_rows*. See the module-level
    rationale above for why this relative rule is used instead of an absolute
    staleness threshold.

    Returns an empty list when every direction is active, when all of them are
    quiet together, or when fewer than two directions are supplied (there is no
    peer to compare against).
    """
    if len(directions) < 2:
        return []
    activity = {a.direction: a.rows for a in summarise_direction_activity(
        directions=directions, now=now, window=window, stats_dir=stats_dir
    )}
    silent: list[str] = []
    for direction in directions:
        if activity.get(direction, 0) != 0:
            continue
        peer_best = max(
            (activity.get(other, 0) for other in directions if other != direction),
            default=0,
        )
        if peer_best >= min_peer_rows:
            silent.append(direction)
    return silent


__all__ = [
    "AUSFAELLE_HEADER",
    "DIRECTION_SILENCE_MIN_PEER_ROWS",
    "DIRECTION_SILENCE_WINDOW_HOURS",
    "DirectionActivity",
    "STAMMSTRECKE_DIRECTIONS",
    "find_silent_directions",
    "summarise_direction_activity",
    "HeldRows",
    "rows_held_back",
    "STAMMSTRECKE_HEADER",
    "STOERUNGEN_HEADER",
    "WEEKDAY_LABELS",
    "DEFAULT_STATS_DIR",
    "MAX_LABELLED_SCAN_CHARS",
    "MAX_STAMMSTRECKE_CSV_BYTES",
    "StammstreckeObservation",
    "VIENNA_TZ",
    "append_ausfall_row",
    "append_stammstrecke_row",
    "append_disruption_row",
    "current_location_label",
    "extract_location_name",
    "read_recent_stammstrecke_observations",
    "stats_path",
    "to_vienna",
    "_CSV_CONTROL_CHARS_RE",
    "_CSV_FORMULA_PREFIXES",
    "_CSV_TEXT_FIELD_MAX_LEN",
    "_sanitize_csv_text_field",
    "_BETWEEN_RE",
    "_GENERIC_DIRECTORY_TOKENS",
    "_HALTESTELLE_RE",
    "_LABELED_LOCATION_RE",
    "_MAX_STATION_WINDOW",
    "_NOISE_TOKEN_RE",
    "_RICHTUNG_RE",
    "_TOKEN_SPLIT_RE",
    "_WIEN_PREFIX_RE",
    "_normalise_location",
    "_resolve_via_directory",
    "_scan_for_directory_station",
    "_parse_stammstrecke_row",
]
