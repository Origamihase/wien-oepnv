#!/usr/bin/env python3
"""Generate the Stammstrecke + Störungen statistics dashboard.

Reads the append-only CSV ledgers under ``data/stats/`` (written by
:mod:`scripts.update_stammstrecke_status` and :mod:`src.build_feed`),
aggregates them by weekday / hour / location, and writes a single
Markdown report to ``docs/statistik.md``.

The script is **strictly zero-dependency** — only the Python standard
library is used (``csv``, ``collections``, ``datetime``, ``statistics``,
``pathlib``, ``zoneinfo``, ``argparse``). No NumPy / Pandas /
Matplotlib. ASCII / Emoji bar charts are rendered inline so the report
is readable as plain text in a terminal as well as in any Markdown
viewer.

Design contract
---------------

* **Read-only on the inputs**: the script *never* modifies the input
  CSVs. Corruption-tolerance is provided by skipping malformed rows
  (logged at WARNING) instead of crashing — a single fat-fingered
  manual edit can never break the dashboard regeneration.
* **Idempotent on the output**: running the script twice on the same
  data produces byte-identical Markdown. Any aggregation step that is
  order-sensitive (top-N location ranking) breaks ties with a stable
  secondary sort on the location name itself.
* **Bounded read sizes**: each CSV file is capped at
  :data:`MAX_CSV_BYTES` (~25 MiB) on read. A planted-huge file is
  skipped with a WARNING instead of buffered into memory.
* **Atomic write**: the dashboard is written via
  :func:`src.utils.files.atomic_write` so a crash or kill-signal
  mid-write cannot leave a half-rendered Markdown report on disk.
* **Timezone**: all aggregation honours ``Europe/Vienna`` — the source
  CSVs already store timestamps in that zone (the writers normalise
  via :func:`src.utils.stats.to_vienna`), so the script just trusts
  the recorded values.
"""
from __future__ import annotations

import argparse
import csv
import io
import logging
import re
import statistics
import sys
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final, TypeVar
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.feed.logging_safe import setup_script_logging  # noqa: E402
from src.utils.files import atomic_write, read_capped_text  # noqa: E402
from src.utils.logging import sanitize_log_arg  # noqa: E402
from src.utils.stats import (  # noqa: E402
    AUSFAELLE_HEADER,
    DEFAULT_STATS_DIR,
    STAMMSTRECKE_HEADER,
    STOERUNGEN_HEADER,
    WEEKDAY_LABELS,
    stats_path,
)
from src.feed.stammstrecke import DELAY_THRESHOLD_MINUTES, FEED_WINDOW  # noqa: E402
from src.utils.text import (  # noqa: E402
    escape_markdown_cell,
    normalise_markdown_text,
)

LOGGER = logging.getLogger("generate_markdown_stats")

VIENNA_TZ: Final = ZoneInfo("Europe/Vienna")

DEFAULT_OUTPUT_PATH: Final = REPO_ROOT / "docs" / "statistik.md"
DEFAULT_README_PATH: Final = REPO_ROOT / "README.md"

# Cap each CSV at ~25 MiB on read. At ~80 bytes per row this allows
# >300 000 rows per year — far above any realistic Stammstrecke /
# disruption log-rate. A file that exceeds the cap is treated as
# corrupted / planted and skipped; the dashboard still renders from
# whatever other inputs are available.
MAX_CSV_BYTES: Final = 25 * 1024 * 1024

# Cap the README at 1 MiB on read. The current file is ~5 KiB; the cap
# defends the patcher against an oversized / planted README that would
# otherwise be buffered into memory verbatim. Mirrors the canonical
# capped-read pattern from ``src.utils.files.read_capped_text``.
README_MAX_BYTES: Final = 1 * 1024 * 1024

# Window for the README snapshot ("Aktueller Schnappschuss"). The full
# annual dashboard remains at ``docs/statistik.md``; the README block is
# intentionally short so it stays glanceable.
DEFAULT_README_WINDOW_DAYS: Final = 30
STAMMSTRECKE_THRESHOLD_MINUTES: Final = 9.0
README_PENDING_PLACEHOLDER: Final = "_wird berechnet…_"

# Sentinel emitted by :func:`src.utils.stats.extract_location_name` when
# no directory-anchored station can be identified for a disruption (the
# upstream title/description simply doesn't mention a station name we
# recognise). The Top-N "Häufigste Störungsorte" / "Hotspots" rankings
# explicitly skip this bucket — without the filter, every period in
# which most disruptions are line-only mentions ("Demonstration auf
# Linie 5") would surface ``unbekannt`` as the #1 hotspot, which is not
# useful operator information. The bucket is still counted in the
# overall "Erfasste Störungen" total and in the weekday / hour
# distributions so the temporal signal is preserved.
LOCATION_UNKNOWN: Final = "unbekannt"

# Bar-chart geometry. The bar widths are intentionally short so the
# rendered Markdown stays comfortable even on a 96-col terminal viewer.
MAX_BAR_WIDTH: Final = 24

# Per-cell length cap applied at every CSV-derived Markdown sink. Each
# CSV writer in :mod:`src.utils.stats` already caps ``provider`` /
# ``location_name`` / ``direction`` at 200 chars on persistence, but
# the dashboard renders inside narrow Markdown table columns; an
# additional render-side cap keeps the layout legible even if a future
# writer relaxes its own cap.
_DASHBOARD_FIELD_MAX_LEN: Final = 80

# Visual vocabulary. Different glyphs per chart type so the eye can
# tell them apart at a glance even when the dashboard is rendered in
# monochrome.
BAR_GLYPHS: Final = {
    "weekday": "🟦",
    "hour": "🟧",
    "delay_weekday": "🟥",
    "delay_hour": "🟨",
    "ausfall_weekday": "🟪",
    "ausfall_hour": "🟫",
}


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
    threshold_minutes: float = 9.0


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
    threshold_minutes: float = 9.0,
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
        line_count[row.line] += 1

    return AusfallAggregate(
        by_weekday=dict(weekday_count),
        by_hour=dict(hour_count),
        by_direction=dict(direction_count),
        by_line=dict(line_count),
        total_cancellations=len(rows),
    )


# ---- Bar rendering ---------------------------------------------------------


def _scale_bar(value: float, max_value: float, *, width: int = MAX_BAR_WIDTH) -> int:
    """Return the integer block count for *value* relative to *max_value*.

    A non-zero value always renders as at least one block so a faint
    signal does not vanish entirely. Zero stays zero.
    """
    if max_value <= 0 or value <= 0:
        return 0
    raw = (value / max_value) * width
    return max(1, min(width, int(round(raw))))


def _bar_line(
    label: str,
    value: float,
    max_value: float,
    glyph: str,
    *,
    label_width: int = 12,
    suffix: str = "",
    width: int = MAX_BAR_WIDTH,
) -> str:
    """Render a single ``label │ ████░░░░ 12.3`` chart row."""
    blocks = _scale_bar(value, max_value, width=width)
    bar = (glyph * blocks) + ("·" * (width - blocks))
    padded_label = label.ljust(label_width)
    return f"`{padded_label}` │ {bar} {suffix}".rstrip()


def render_weekday_bars(
    counts: dict[str, int],
    *,
    glyph: str,
    title: str,
) -> list[str]:
    """Render a Mo-So weekday chart with monotone glyphs."""
    if not counts:
        return [f"### {title}", "", "_Keine Daten verfügbar._", ""]
    max_value = max(counts.values())
    lines: list[str] = [f"### {title}", "", "```"]
    for label in WEEKDAY_LABELS:
        value = counts.get(label, 0)
        suffix = f" {value}" if value else " 0"
        lines.append(_bar_line(label, value, max_value, glyph, label_width=4, suffix=suffix))
    lines.append("```")
    lines.append("")
    return lines


def render_hour_bars(
    counts: dict[int, int],
    *,
    glyph: str,
    title: str,
) -> list[str]:
    """Render a 0-23 hour chart with monotone glyphs."""
    if not counts:
        return [f"### {title}", "", "_Keine Daten verfügbar._", ""]
    max_value = max(counts.values())
    lines: list[str] = [f"### {title}", "", "```"]
    for hour in range(24):
        value = counts.get(hour, 0)
        suffix = f" {value}" if value else " 0"
        lines.append(
            _bar_line(f"{hour:02d}h", value, max_value, glyph, label_width=4, suffix=suffix)
        )
    lines.append("```")
    lines.append("")
    return lines


def render_avg_delay_weekday(avgs: dict[str, float], glyph: str) -> list[str]:
    """Render the weekday breakdown of *average* delay."""
    if not avgs:
        return [
            "### Verspätungen ⌀ Minuten je Wochentag",
            "",
            "_Keine Daten verfügbar._",
            "",
        ]
    max_value = max(avgs.values())
    lines: list[str] = [
        "### Verspätungen ⌀ Minuten je Wochentag",
        "",
        "```",
    ]
    for label in WEEKDAY_LABELS:
        value = avgs.get(label, 0.0)
        suffix = f" {value:5.1f} min" if value else "  0.0 min"
        lines.append(
            _bar_line(label, value, max_value, glyph, label_width=4, suffix=suffix)
        )
    lines.append("```")
    lines.append("")
    return lines


def render_avg_delay_hour(avgs: dict[int, float], glyph: str) -> list[str]:
    """Render the hour-of-day breakdown of *average* delay."""
    if not avgs:
        return [
            "### Verspätungen ⌀ Minuten je Stunde",
            "",
            "_Keine Daten verfügbar._",
            "",
        ]
    max_value = max(avgs.values())
    lines: list[str] = [
        "### Verspätungen ⌀ Minuten je Stunde",
        "",
        "```",
    ]
    for hour in range(24):
        value = avgs.get(hour, 0.0)
        suffix = f" {value:5.1f} min" if value else "  0.0 min"
        lines.append(
            _bar_line(
                f"{hour:02d}h", value, max_value, glyph, label_width=4, suffix=suffix
            )
        )
    lines.append("```")
    lines.append("")
    return lines


# ---- Markdown assembly -----------------------------------------------------


def _format_summary_section(
    *,
    year: int,
    generated_at: datetime,
    stammstrecke: StammstreckeAggregate,
    stoerungen: StoerungAggregate,
    ausfaelle: AusfallAggregate,
) -> list[str]:
    """Render the top-of-report key metrics block.

    Every cell in this block is scoped to the calendar *year*. The
    rolling 30-day window only feeds the README snapshot — keeping
    the two views distinct (year vs. 30 days) avoids the same data
    point being summed into both numbers at once.
    """
    if stammstrecke.total_observations:
        # Observation-weighted (micro) mean over all rows of *year*.
        # Multiplying each per-weekday mean by its observation count
        # recovers the original delay sum exactly without re-iterating
        # the raw rows, which keeps the rendering side-effect-free on
        # the aggregate input.
        total = sum(
            stammstrecke.by_weekday_avg[wd] * stammstrecke.by_weekday_count[wd]
            for wd in stammstrecke.by_weekday_count
        )
        avg_cell = f"{total / stammstrecke.total_observations:.1f} min"
    else:
        avg_cell = "_keine Daten_"

    return [
        f"# Wien ÖPNV — Statistik {year}",
        "",
        f"_Automatisch erzeugt am {generated_at.isoformat(timespec='minutes')} (Europe/Vienna)._",
        "",
        "## Kennzahlen auf einen Blick",
        "",
        "| Kennzahl | Wert |",
        "| --- | ---: |",
        f"| Stammstrecke-Beobachtungen ({year}) | {stammstrecke.total_observations} |",
        f"| Verspätungen > {stammstrecke.threshold_minutes:g} min ({year}) | "
        f"{stammstrecke.threshold_exceedances} |",
        f"| ⌀ Verspätung ({year}) | {avg_cell} |",
        f"| Stammstrecke-Ausfälle ({year}) | {ausfaelle.total_cancellations} |",
        f"| Erfasste Störungen ({year}) | {stoerungen.total_disruptions} |",
        "",
    ]


def _format_directions_section(stammstrecke: StammstreckeAggregate) -> list[str]:
    """Render the per-direction breakdown table.

    Routes ``direction`` through :func:`normalise_markdown_text` +
    :func:`escape_markdown_cell` so a CSV row whose direction field
    contains a Markdown-meaningful character (``|`` / ``<`` / `` ` ``
    / ``[`` / embedded newline) cannot break out of the 2-column
    table cell. See the module-level threat model in the Sentinel
    journal (2026-05-09 Markdown sibling drift round).
    """
    if not stammstrecke.by_direction:
        return ["### Beobachtungen je Richtung", "", "_Keine Daten._", ""]
    items = sorted(
        stammstrecke.by_direction.items(),
        key=lambda pair: (-pair[1], pair[0]),
    )
    lines: list[str] = ["### Beobachtungen je Richtung", "", "| Richtung | Anzahl |", "| --- | ---: |"]
    for direction, count in items:
        cell = escape_markdown_cell(
            normalise_markdown_text(direction, max_len=_DASHBOARD_FIELD_MAX_LEN)
        )
        lines.append(f"| {cell} | {count} |")
    lines.append("")
    return lines


def _format_providers_section(stoerungen: StoerungAggregate) -> list[str]:
    """Render the per-provider breakdown table.

    See :func:`_format_directions_section` for the threat model.
    The ``provider`` field flows from
    :data:`cache/wl/wl_baustellen.json["source"]` (and siblings) which
    a poisoned cache file can populate verbatim — defending the
    rendering boundary closes that path even when the upstream cache
    integrity check is bypassed.
    """
    if not stoerungen.by_provider:
        return ["### Störungen je Quelle", "", "_Keine Daten._", ""]
    items = sorted(
        stoerungen.by_provider.items(),
        key=lambda pair: (-pair[1], pair[0]),
    )
    lines: list[str] = ["### Störungen je Quelle", "", "| Quelle | Anzahl |", "| --- | ---: |"]
    for provider, count in items:
        cell = escape_markdown_cell(
            normalise_markdown_text(provider, max_len=_DASHBOARD_FIELD_MAX_LEN)
        )
        lines.append(f"| {cell} | {count} |")
    lines.append("")
    return lines


def _format_ausfall_directions_section(ausfaelle: AusfallAggregate) -> list[str]:
    """Render the per-direction breakdown of cancellations.

    Same threat model as :func:`_format_directions_section` — the
    ``direction`` field flows from the CSV writer's
    :func:`src.utils.stats._sanitize_csv_text_field` defence and then
    through :func:`normalise_markdown_text` / :func:`escape_markdown_cell`
    at the rendering boundary.
    """
    if not ausfaelle.by_direction:
        return ["### Ausfälle je Richtung", "", "_Keine Ausfälle erfasst._", ""]
    items = sorted(
        ausfaelle.by_direction.items(),
        key=lambda pair: (-pair[1], pair[0]),
    )
    lines: list[str] = [
        "### Ausfälle je Richtung",
        "",
        "| Richtung | Anzahl |",
        "| --- | ---: |",
    ]
    for direction, count in items:
        cell = escape_markdown_cell(
            normalise_markdown_text(direction, max_len=_DASHBOARD_FIELD_MAX_LEN)
        )
        lines.append(f"| {cell} | {count} |")
    lines.append("")
    return lines


def _format_ausfall_lines_section(ausfaelle: AusfallAggregate) -> list[str]:
    """Render the per-line breakdown of cancellations.

    Surfaces "which S-Bahn / REX line cancels most often?" — the
    primary operational signal for cancellations. The ``line`` field
    is the canonicalised VAO line designation (``S1``, ``REX3`` …)
    written by the collectors via
    :func:`src.utils.stats._sanitize_csv_text_field`, so the rendering
    boundary uses the same Markdown escaping pipeline as the direction
    cells.
    """
    if not ausfaelle.by_line:
        return ["### Ausfälle je Linie", "", "_Keine Ausfälle erfasst._", ""]
    items = sorted(
        ausfaelle.by_line.items(),
        key=lambda pair: (-pair[1], pair[0]),
    )
    lines: list[str] = [
        "### Ausfälle je Linie",
        "",
        "| Linie | Anzahl |",
        "| --- | ---: |",
    ]
    for line, count in items:
        cell = escape_markdown_cell(
            normalise_markdown_text(line, max_len=_DASHBOARD_FIELD_MAX_LEN)
        )
        lines.append(f"| {cell} | {count} |")
    lines.append("")
    return lines


def render_markdown(
    *,
    year: int,
    generated_at: datetime,
    stammstrecke: StammstreckeAggregate,
    stoerungen: StoerungAggregate,
    ausfaelle: AusfallAggregate,
) -> str:
    """Compose the full Markdown dashboard string from the aggregates."""
    sections: list[str] = []
    sections.extend(
        _format_summary_section(
            year=year,
            generated_at=generated_at,
            stammstrecke=stammstrecke,
            stoerungen=stoerungen,
            ausfaelle=ausfaelle,
        )
    )

    sections.extend(["## Stammstrecke", ""])
    sections.extend(_format_directions_section(stammstrecke))
    sections.extend(
        render_weekday_bars(
            stammstrecke.by_weekday_count,
            glyph=BAR_GLYPHS["weekday"],
            title="Beobachtungen je Wochentag",
        )
    )
    sections.extend(
        render_hour_bars(
            stammstrecke.by_hour_count,
            glyph=BAR_GLYPHS["hour"],
            title="Beobachtungen je Stunde",
        )
    )
    sections.extend(
        render_avg_delay_weekday(
            stammstrecke.by_weekday_avg, BAR_GLYPHS["delay_weekday"]
        )
    )
    sections.extend(
        render_avg_delay_hour(stammstrecke.by_hour_avg, BAR_GLYPHS["delay_hour"])
    )

    sections.extend(["## Ausfälle", ""])
    sections.extend(_format_ausfall_directions_section(ausfaelle))
    sections.extend(_format_ausfall_lines_section(ausfaelle))
    sections.extend(
        render_weekday_bars(
            ausfaelle.by_weekday,
            glyph=BAR_GLYPHS["ausfall_weekday"],
            title="Ausfälle je Wochentag",
        )
    )
    sections.extend(
        render_hour_bars(
            ausfaelle.by_hour,
            glyph=BAR_GLYPHS["ausfall_hour"],
            title="Ausfälle je Stunde",
        )
    )

    sections.extend(["## Störungen", ""])
    sections.extend(_format_providers_section(stoerungen))
    sections.extend(
        render_weekday_bars(
            stoerungen.by_weekday,
            glyph=BAR_GLYPHS["weekday"],
            title="Störungen je Wochentag",
        )
    )
    sections.extend(
        render_hour_bars(
            stoerungen.by_hour,
            glyph=BAR_GLYPHS["hour"],
            title="Störungen je Stunde",
        )
    )

    sections.extend(
        [
            "---",
            "",
            "_Quellen_: `data/stats/stammstrecke_*.csv`, "
            "`data/stats/ausfaelle_*.csv`, `data/stats/stoerungen_*.csv`. "
            "Generiert von `scripts/generate_markdown_stats.py`.",
            "",
        ]
    )

    return "\n".join(sections).rstrip() + "\n"


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


def _format_thousands(value: int) -> str:
    """Format *value* with German thousands separator ('.')."""
    return f"{value:,}".replace(",", ".")


def _format_window_timestamp(now: datetime) -> str:
    """Render the "Letzte Aktualisierung" cell as ``YYYY-MM-DD HH:MM TZ``."""
    return now.strftime("%Y-%m-%d %H:%M %Z").rstrip()


def render_readme_stammstrecke_live_block(
    rows: list[StammstreckeRow],
    *,
    now: datetime,
) -> str:
    """Render the inner content of the ``STATS:STAMMSTRECKE_LIVE`` README block.

    Returns the body that goes *between* the two HTML-comment markers,
    terminated by a newline so the closing marker stays on its own line.
    """
    threshold_label = (
        f"{DELAY_THRESHOLD_MINUTES:.0f}"
        if DELAY_THRESHOLD_MINUTES == int(DELAY_THRESHOLD_MINUTES)
        else f"{DELAY_THRESHOLD_MINUTES:g}"
    )
    header = (
        "> _Letzte 60 Minuten – automatisch aktualisiert vom Workflow_ [`update-cycle.yml`](.github/workflows/update-cycle.yml).\n"
        "\n"
        "| Kennzahl | Wert |\n"
        "| -------- | ---- |\n"
    )
    if not rows:
        return (
            header
            + "| Beobachtungen (gesamt) | 0 |\n"
            + "| Durchschnittliche Verspätung | N/A |\n"
            + f"| Kritische Verspätungen (> {threshold_label} min) | 0 |\n"
            + f"| Letzte Aktualisierung | {now.strftime('%Y-%m-%d %H:%M %Z')} |\n"
        )

    count = len(rows)
    mean_delay = statistics.mean(r.delay_minutes for r in rows)
    critical_count = sum(1 for r in rows if r.delay_minutes > DELAY_THRESHOLD_MINUTES)

    return (
        header
        + f"| Beobachtungen (gesamt) | {count} |\n"
        + f"| Durchschnittliche Verspätung | {mean_delay:.1f} min |\n"
        + f"| Kritische Verspätungen (> {threshold_label} min) | {critical_count} |\n"
        + f"| Letzte Aktualisierung | {now.strftime('%Y-%m-%d %H:%M %Z')} |\n"
    )


def render_readme_stammstrecke_block(
    rows: list[StammstreckeRow],
    *,
    now: datetime,
    window_days: int = DEFAULT_README_WINDOW_DAYS,
    threshold_minutes: float = STAMMSTRECKE_THRESHOLD_MINUTES,
) -> str:
    """Render the inner content of the ``STATS:STAMMSTRECKE`` README block.

    Returns the body that goes *between* the two HTML-comment markers,
    terminated by a newline so the closing marker stays on its own line.
    Empty input renders the canonical ``wird berechnet…`` placeholder so
    a workflow run on a brand-new repo (no CSVs yet) still produces a
    well-formed Markdown table.

    The displayed delay is the *arithmetic mean* across the window —
    the feed-event trigger uses the median over the same observations
    (defensive against single sample-level outliers) but the README
    cell shows the average, which is more intuitive for non-technical
    readers. The "Kritische Verspätungen" counter counts each *row*
    whose persisted per-sample mean delay exceeds the threshold — at
    most once per cron cycle, so the same physical cycle is never
    multiplied into the count.
    """
    threshold_label = (
        f"{threshold_minutes:.0f}"
        if threshold_minutes == int(threshold_minutes)
        else f"{threshold_minutes:g}"
    )
    header = (
        f"> _Letzte {window_days} Tage – automatisch aktualisiert vom Workflow_ "
        "[`update-cycle.yml`](.github/workflows/update-cycle.yml).\n"
        "\n"
        "| Kennzahl | Wert |\n"
        "| -------- | ---- |\n"
    )
    if not rows:
        return (
            header
            + f"| Beobachtungen (gesamt) | {README_PENDING_PLACEHOLDER} |\n"
            + f"| Durchschnittliche Verspätung | {README_PENDING_PLACEHOLDER} |\n"
            + f"| Kritische Verspätungen (> {threshold_label} min) | "
            + f"{README_PENDING_PLACEHOLDER} |\n"
            + f"| Letzte Aktualisierung | {_format_window_timestamp(now)} |\n"
        )
    delays = [r.delay_minutes for r in rows]
    avg_delay = statistics.mean(delays)
    exceedances = sum(1 for d in delays if d > threshold_minutes)
    return (
        header
        + f"| Beobachtungen (gesamt) | {_format_thousands(len(rows))} |\n"
        + f"| Durchschnittliche Verspätung | {avg_delay:.1f} min |\n"
        + f"| Kritische Verspätungen (> {threshold_label} min) | "
        + f"{_format_thousands(exceedances)} |\n"
        + f"| Letzte Aktualisierung | {_format_window_timestamp(now)} |\n"
    )


def render_readme_ausfaelle_live_block(
    rows: list[AusfallRow],
    *,
    now: datetime,
) -> str:
    """Render the inner content of the ``STATS:AUSFAELLE_LIVE`` README block.

    The live block reflects the rolling 60-minute window — i.e., the
    same :data:`FEED_WINDOW` the delay block uses — so the two
    snapshots are scoped to the same observation horizon. Most ticks
    will show ``0``; that explicit zero is the operational signal
    "currently no cancellations on the Stammstrecke", which is more
    useful than hiding the row entirely on a quiet day.
    """

    header = (
        "> _Letzte 60 Minuten – automatisch aktualisiert vom Workflow_ "
        "[`update-cycle.yml`](.github/workflows/update-cycle.yml).\n"
        "\n"
        "| Kennzahl | Wert |\n"
        "| -------- | ---- |\n"
    )
    return (
        header
        + f"| Ausfälle (gesamt) | {len(rows)} |\n"
        + f"| Letzte Aktualisierung | {now.strftime('%Y-%m-%d %H:%M %Z')} |\n"
    )


def render_readme_ausfaelle_block(
    rows: list[AusfallRow],
    *,
    now: datetime,
    window_days: int = DEFAULT_README_WINDOW_DAYS,
) -> str:
    """Render the inner content of the ``STATS:AUSFAELLE`` README block.

    30-day rolling cancellation count for the README snapshot. Returns
    the body that goes *between* the two HTML-comment markers,
    terminated by a newline so the closing marker stays on its own
    line. The block is always rendered (even at zero cancellations)
    because an explicit ``0`` is the operationally-meaningful "stable
    service" signal — hiding it would conflate "stable" with "data
    missing".
    """

    header = (
        f"> _Letzte {window_days} Tage – automatisch aktualisiert vom Workflow_ "
        "[`update-cycle.yml`](.github/workflows/update-cycle.yml).\n"
        "\n"
        "| Kennzahl | Wert |\n"
        "| -------- | ---- |\n"
    )
    # Sorted by count (descending) so the most-cancelled line surfaces
    # first; the secondary sort by line name keeps the rendering
    # stable when two lines tie. Limited to top 3 to keep the README
    # block compact.
    line_count: dict[str, int] = defaultdict(int)
    for row in rows:
        line_count[row.line] += 1
    top_lines = sorted(
        line_count.items(), key=lambda pair: (-pair[1], pair[0])
    )[:3]
    if top_lines:
        top_lines_cell = ", ".join(
            f"{escape_markdown_cell(normalise_markdown_text(line, max_len=_DASHBOARD_FIELD_MAX_LEN))} ({count})"
            for line, count in top_lines
        )
    else:
        top_lines_cell = "_keine_"
    return (
        header
        + f"| Ausfälle (gesamt) | {_format_thousands(len(rows))} |\n"
        + f"| Häufigste Linien | {top_lines_cell} |\n"
        + f"| Letzte Aktualisierung | {_format_window_timestamp(now)} |\n"
    )


# Marker pair contract: the patcher rewrites whatever sits between
# ``<!-- STATS:<NAME>:BEGIN -->`` and ``<!-- STATS:<NAME>:END -->``.
# Both markers MUST appear verbatim and on their own logical line in
# the README; markers nested inside fenced code blocks would break the
# regex (deliberately — a marker that survives a copy-paste into a code
# block was almost certainly an accident, and silent-rewriting it would
# corrupt user-authored documentation). The regex compiles non-greedy
# / DOTALL so a single sweep handles every named marker without
# back-tracking trouble.
_README_MARKER_RE_TEMPLATE: Final = (
    r"(<!-- STATS:{name}:BEGIN -->)(.*?)(<!-- STATS:{name}:END -->)"
)


def _build_marker_re(name: str) -> re.Pattern[str]:
    return re.compile(
        _README_MARKER_RE_TEMPLATE.format(name=re.escape(name)),
        re.DOTALL,
    )


def patch_readme_stats(
    readme_path: Path,
    sections: dict[str, str],
) -> bool:
    """Patch *readme_path* so each ``STATS:<name>:`` marker pair wraps the
    matching body.

    The patcher is **idempotent** in the byte-equality sense: a second
    call with identical *sections* produces the same file (and,
    crucially, when the would-be result equals the on-disk content the
    file is *not* touched — its mtime is preserved so the auto-commit
    action correctly sees a no-op).

    Missing markers are logged at WARNING and the section is skipped —
    the rest of the README is preserved untouched. A README that fails
    to load (missing / oversize / non-UTF8) is treated as a no-op so
    the dashboard generation still succeeds; the warning is the
    audit trail.

    Returns ``True`` when the file was rewritten, ``False`` when nothing
    changed (or when the file could not be read).
    """
    raw = read_capped_text(
        readme_path,
        max_bytes=README_MAX_BYTES,
        label="README",
        logger=LOGGER,
    )
    if raw is None:
        return False
    new_text = raw
    for name, body in sections.items():
        pattern = _build_marker_re(name)
        match = pattern.search(new_text)
        if match is None:
            LOGGER.warning(
                "README-Marker STATS:%s nicht gefunden in %s — überspringe Section.",
                sanitize_log_arg(name),
                sanitize_log_arg(str(readme_path)),
            )
            continue
        replacement = f"{match.group(1)}\n{body}{match.group(3)}"
        new_text = new_text[: match.start()] + replacement + new_text[match.end():]
    if new_text == raw:
        LOGGER.info(
            "README-Statistik-Block unverändert (%s) — kein Schreibvorgang.",
            sanitize_log_arg(str(readme_path)),
        )
        return False
    readme_path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(
        readme_path,
        mode="w",
        encoding="utf-8",
        permissions=0o644,
    ) as fh:
        fh.write(new_text)
    LOGGER.info(
        "README-Statistik-Block aktualisiert (%s, %d Bytes).",
        sanitize_log_arg(str(readme_path)),
        len(new_text.encode("utf-8")),
    )
    return True


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


def write_dashboard(
    markdown: str,
    *,
    output_path: Path = DEFAULT_OUTPUT_PATH,
) -> None:
    """Atomically write *markdown* to *output_path*.

    Uses :func:`src.utils.files.atomic_write` so a partially-written
    dashboard cannot replace the previous one.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(output_path, mode="w", encoding="utf-8", permissions=0o644) as fh:
        fh.write(markdown)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns ``0`` on success (incl. the empty-data case), ``1`` on a
    fatal error (write failure). Parsing errors of individual CSV rows
    are tolerated and never propagate to the exit code — the dashboard
    is regenerated from whatever can be parsed.
    """
    setup_script_logging(logging.INFO)

    parser = argparse.ArgumentParser(
        description="Generate the Wien ÖPNV statistics dashboard.",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=datetime.now(VIENNA_TZ).year,
        help="Calendar year to aggregate (default: current Vienna year).",
    )
    parser.add_argument(
        "--stats-dir",
        type=Path,
        default=DEFAULT_STATS_DIR,
        help=f"Directory containing the stats CSV files (default: {DEFAULT_STATS_DIR}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=f"Output Markdown path (default: {DEFAULT_OUTPUT_PATH}).",
    )
    parser.add_argument(
        "--readme-path",
        type=Path,
        default=DEFAULT_README_PATH,
        help=(
            "Path to the README.md whose <!-- STATS:* --> markers should be "
            f"patched (default: {DEFAULT_README_PATH})."
        ),
    )
    parser.add_argument(
        "--readme-window-days",
        type=int,
        default=DEFAULT_README_WINDOW_DAYS,
        help=(
            "Window size in days for the README snapshot block "
            f"(default: {DEFAULT_README_WINDOW_DAYS})."
        ),
    )
    parser.add_argument(
        "--skip-readme",
        action="store_true",
        help=(
            "Skip the README patch entirely (only the docs/statistik.md "
            "dashboard is regenerated)."
        ),
    )
    parser.add_argument(
        "--skip-dashboard",
        action="store_true",
        help=(
            "Skip regenerating docs/statistik.md (only the README STATS "
            "markers are patched). Inverse of ``--skip-readme``; used by "
            "``update-cycle.yml`` so the dashboard refreshes once daily "
            "at the 00:00 Europe/Vienna tick while the README snapshot "
            "continues to update on every 30-min tick."
        ),
    )
    parser.add_argument(
        "--now-iso",
        type=str,
        default=None,
        help=(
            "Override the wall clock used for the README window cutoff and "
            "the 'Letzte Aktualisierung' cell. Accepts an ISO 8601 string; "
            "missing tzinfo is interpreted as Europe/Vienna. Intended for "
            "deterministic reproductions and tests."
        ),
    )
    args = parser.parse_args(argv)

    if args.readme_window_days < 1:
        LOGGER.error(
            "--readme-window-days muss >= 1 sein, war %d.",
            args.readme_window_days,
        )
        return 1

    if args.now_iso is not None:
        try:
            now = datetime.fromisoformat(args.now_iso)
        except ValueError:
            LOGGER.error(
                "--now-iso konnte nicht geparst werden: %s",
                sanitize_log_arg(args.now_iso),
            )
            return 1
        if now.tzinfo is None:
            # Naive --now-iso input is interpreted as Europe/Vienna (the
            # project's canonical zone — every CSV writer normalises to
            # it via :func:`src.utils.stats.to_vienna`).
            now = now.replace(tzinfo=VIENNA_TZ)
        else:
            # Convert to the named ZoneInfo so ``strftime('%Z')`` renders
            # the friendly abbreviation ("CEST" / "CET") rather than the
            # raw offset ("UTC+02:00") that ``datetime.fromisoformat``
            # would otherwise carry through.
            now = now.astimezone(VIENNA_TZ)
    else:
        now = datetime.now(VIENNA_TZ)

    sm_rows, st_rows, au_rows = collect_year_data(
        args.year, stats_dir=args.stats_dir
    )
    LOGGER.info(
        "Stats geladen: %d Stammstrecke-Zeilen, %d Störungs-Zeilen, "
        "%d Ausfall-Zeilen aus %s.",
        len(sm_rows),
        len(st_rows),
        len(au_rows),
        sanitize_log_arg(str(args.stats_dir)),
    )

    if args.skip_dashboard:
        # ``update-cycle.yml`` passes this flag on every 30-min tick
        # except the one that lands inside the 00:00 Europe/Vienna hour.
        # Skipping the aggregation + render avoids the unnecessary
        # CPU + write work (and prevents the 30-min churn of the
        # ``_Automatisch erzeugt am ..._`` timestamp leaking into the
        # commit log).
        LOGGER.info(
            "Dashboard-Schritt übersprungen (--skip-dashboard) — "
            "README wird weiterhin gepatcht."
        )
    else:
        sm_agg = aggregate_stammstrecke(sm_rows)
        st_agg = aggregate_stoerungen(st_rows)
        au_agg = aggregate_ausfaelle(au_rows)

        markdown = render_markdown(
            year=args.year,
            generated_at=now,
            stammstrecke=sm_agg,
            stoerungen=st_agg,
            ausfaelle=au_agg,
        )

        try:
            write_dashboard(markdown, output_path=args.output)
        except OSError as exc:
            LOGGER.error(
                "Konnte Dashboard nicht schreiben (%s): %s",
                sanitize_log_arg(str(args.output)),
                sanitize_log_arg(str(exc)),
            )
            return 1

        LOGGER.info(
            "Dashboard geschrieben: %s (%d Bytes).",
            sanitize_log_arg(str(args.output)),
            len(markdown.encode("utf-8")),
        )

    if args.skip_readme:
        return 0

    # Load the cross-year Stammstrecke window for the README snapshot.
    # The nightly workflow runs at 00:15 Europe/Vienna, so a 30-day
    # cutoff in early January legitimately spans the previous calendar
    # year. ``collect_year_data`` returns empty lists for missing files,
    # so eagerly loading both years is safe even mid-year.
    cutoff = now - timedelta(days=args.readme_window_days)
    extra_years = sorted({cutoff.year, now.year} - {args.year})
    window_sm: list[StammstreckeRow] = list(sm_rows)
    window_au: list[AusfallRow] = list(au_rows)
    for extra_year in extra_years:
        extra_sm, _, extra_au = collect_year_data(
            extra_year, stats_dir=args.stats_dir
        )
        window_sm.extend(extra_sm)
        window_au.extend(extra_au)
    sm_window = _filter_rows_by_window(
        window_sm, days=args.readme_window_days, now=now
    )
    au_window = _filter_rows_by_window(
        window_au, days=args.readme_window_days, now=now
    )
    # Defense-in-depth: only patch the Stammstrecke marker when the
    # window actually carries rows. Without this gate, an unrelated
    # caller of ``main()`` that supplies a stats directory but forgets
    # to override ``--readme-path`` (or pass ``--skip-readme``) would
    # silently overwrite the production ``README.md`` with the
    # ``_wird berechnet…_`` placeholder block — that exact bug bit
    # ``tests/scripts/test_generate_markdown_stats.py`` and stamped
    # placeholders into the committed README on 2026-05-09 (audit
    # trail in PR #1397).
    sections: dict[str, str] = {}
    if sm_window:
        sections["STAMMSTRECKE"] = render_readme_stammstrecke_block(
            sm_window,
            now=now,
            window_days=args.readme_window_days,
        )
    else:
        LOGGER.info(
            "STAMMSTRECKE README block skipped: 0 rows in %d-day window.",
            args.readme_window_days,
        )

    sm_live_window = _filter_rows_by_timedelta(
        window_sm, delta=FEED_WINDOW, now=now
    )
    sections["STAMMSTRECKE_LIVE"] = render_readme_stammstrecke_live_block(
        sm_live_window,
        now=now,
    )
    # Cancellation snapshots: always render — an explicit ``0`` is the
    # operationally-meaningful "stable service" signal, so the README
    # block is patched even when the window contains no cancellations.
    # Gating on ``sm_window``/``sm_live_window`` (delay data present)
    # ensures we still bail to "preserve existing README" when the
    # repository has no statistics at all (fresh clone, missing CSVs).
    au_live_window = _filter_rows_by_timedelta(
        window_au, delta=FEED_WINDOW, now=now
    )
    sections["AUSFAELLE_LIVE"] = render_readme_ausfaelle_live_block(
        au_live_window,
        now=now,
    )
    sections["AUSFAELLE"] = render_readme_ausfaelle_block(
        au_window,
        now=now,
        window_days=args.readme_window_days,
    )
    if not sm_window and not sm_live_window:
        LOGGER.info(
            "README patch skipped entirely: no data for any marker block in "
            "the %d-day window — existing README content preserved.",
            args.readme_window_days,
        )
        return 0
    try:
        patch_readme_stats(args.readme_path, sections)
    except OSError as exc:
        LOGGER.error(
            "Konnte README nicht aktualisieren (%s): %s",
            sanitize_log_arg(str(args.readme_path)),
            sanitize_log_arg(str(exc)),
        )
        return 1

    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())


# Exposed for tests; intentionally module-private otherwise.
__all__ = [
    "DEFAULT_OUTPUT_PATH",
    "DEFAULT_README_PATH",
    "DEFAULT_README_WINDOW_DAYS",
    "LOCATION_UNKNOWN",
    "MAX_CSV_BYTES",
    "README_MAX_BYTES",
    "README_PENDING_PLACEHOLDER",
    "STAMMSTRECKE_THRESHOLD_MINUTES",
    "AusfallAggregate",
    "AusfallRow",
    "StammstreckeAggregate",
    "StammstreckeRow",
    "StoerungAggregate",
    "StoerungRow",
    "aggregate_ausfaelle",
    "aggregate_stammstrecke",
    "aggregate_stoerungen",
    "collect_year_data",
    "main",
    "patch_readme_stats",
    "render_hour_bars",
    "render_markdown",
    "render_readme_ausfaelle_block",
    "render_readme_ausfaelle_live_block",
    "render_readme_stammstrecke_block",
    "render_weekday_bars",
    "write_dashboard",
]


# Sanity import check: ensure the WEEKDAY_LABELS constant from
# :mod:`src.utils.stats` matches the local one used for rendering. Drift
# between these would silently mis-bucket weekdays.
if tuple(WEEKDAY_LABELS) != ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"):  # pragma: no cover - drift guard
    raise RuntimeError(
        "WEEKDAY_LABELS drifted between src.utils.stats and the dashboard renderer."
    )
