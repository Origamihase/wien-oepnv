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
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

from src.utils.logging import sanitize_log_arg

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

# German short weekday names (Mo, Di, Mi, …) keyed by ``datetime.weekday()``
# (0 = Monday). The aggregator renders these verbatim, so any change here
# also changes the dashboard labels.
WEEKDAY_LABELS: Final = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")

# Heuristic field-extraction patterns. All patterns operate on the
# already-sanitised plain-text title/description coming out of the
# providers — no HTML, no control bytes, no upstream-controlled
# unbounded length (the providers cap titles at ~200 chars and
# descriptions at a few hundred). The patterns are deliberately broad-
# but-bounded: ``{2,80}`` upper bounds keep ReDoS off the table even on
# pathological input.
_BETWEEN_RE: Final = re.compile(
    r"\bzwischen\s+([A-ZÄÖÜ][\w\.\-’']{1,80}?(?:\s+[A-ZÄÖÜ][\w\.\-’']{1,80}){0,3})"
    r"\s+und\s+",
    re.IGNORECASE,
)
_WIEN_PREFIX_RE: Final = re.compile(
    r"\bWien\s+([A-ZÄÖÜ][\wäöüÄÖÜß\-’']{1,40}(?:\s+[A-ZÄÖÜ][\wäöüÄÖÜß\-’']{1,40})?)"
)
_STATION_NAME_RE: Final = re.compile(
    r"\b([A-ZÄÖÜ][\wäöüÄÖÜß]{2,40}(?:[\-/\s][A-ZÄÖÜ][\wäöüÄÖÜß]{2,40}){0,2})\b"
)
# Words that pass _STATION_NAME_RE but are clearly not station names.
# Intentionally short — only the highest-frequency false positives that
# survive the location heuristic on real provider feeds.
_STOPWORD_LOCATIONS: Final = frozenset(
    {
        "Bauarbeiten",
        "Gleisbauarbeiten",
        "Strassenbauarbeiten",
        "Straßenbauarbeiten",
        "Verkehrsunfall",
        "Verspätung",
        "Verspaetung",
        "Verspätungen",
        "Störung",
        "Stoerung",
        "Störungen",
        "Stoerungen",
        "Ersatzverkehr",
        "Schienenersatzverkehr",
        "Sperre",
        "Sperrung",
        "Aufzug",
        "Rolltreppe",
        "Linie",
        "Linien",
        "Bus",
        "Strassenbahn",
        "Straßenbahn",
        "U-Bahn",
        "S-Bahn",
        "Achtung",
        "Hinweis",
        "Information",
        "Mitteilung",
        "Mo",
        "Di",
        "Mi",
        "Do",
        "Fr",
        "Sa",
        "So",
        "Januar",
        "Februar",
        "März",
        "Maerz",
        "April",
        "Mai",
        "Juni",
        "Juli",
        "August",
        "September",
        "Oktober",
        "November",
        "Dezember",
    }
)


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


def _append_row(
    path: Path,
    header: tuple[str, ...],
    row: tuple[str, ...],
) -> bool:
    """Append *row* to *path*, writing the header on first creation.

    Returns ``True`` on success, ``False`` if any I/O error was caught
    (and logged at WARNING). The boolean return makes the writers
    individually testable without scraping log output.

    Implementation note: ``newline=""`` is the canonical way to disable
    Python's universal-newline translation on writes through
    :mod:`csv`, which would otherwise add a stray ``\\r`` on Windows
    and break round-tripping.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_header = not path.exists() or path.stat().st_size == 0
        with path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            if write_header:
                writer.writerow(header)
            writer.writerow(row)
        try:
            os.chmod(path, 0o644)
        except OSError:
            pass
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
    "Floridsdorf"). *delay_minutes* is the median delay over the
    sampled S-Bahn legs for that direction.

    The function is best-effort: filesystem errors are logged and
    swallowed so the upstream cron pipeline always exits cleanly.
    """
    when = to_vienna(timestamp)
    path = stats_path("stammstrecke", when.year, base_dir=stats_dir)
    row = (
        when.isoformat(timespec="seconds"),
        WEEKDAY_LABELS[when.weekday()],
        f"{when.hour:02d}",
        direction,
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
        provider.strip() or "unbekannt",
        location_name.strip() or "unbekannt",
    )
    return _append_row(path, STOERUNGEN_HEADER, row)


def extract_location_name(item: dict[str, Any]) -> str:
    """Best-effort heuristic to pick a representative location for *item*.

    Tries — in order — the most signal-rich shapes seen in the
    providers' real payloads:

    1. ``zwischen X und Y`` — the X side (canonical ÖBB phrasing for a
       between-stations disruption).
    2. ``Wien <Stadtteil>`` — the prefix is the strongest "this is a
       station name" signal in the corpus.
    3. The first capitalised multi-word token longer than two letters
       that is not in :data:`_STOPWORD_LOCATIONS`. Captures e.g.
       ``Karlsplatz``, ``Floridsdorf``, ``Praterstern``.

    Falls back to ``"unbekannt"`` if nothing matches. The function
    never raises — a malformed item just returns the fallback string.
    """
    title = str(item.get("title") or "")
    description = str(item.get("description") or "")
    haystack = " ".join(part for part in (title, description) if part)
    if not haystack:
        return "unbekannt"

    # Bound the search window: providers cap descriptions at a few
    # hundred chars but a defensive cap also keeps the regex engine
    # bounded on adversarial inputs.
    haystack = haystack[:1024]

    between = _BETWEEN_RE.search(haystack)
    if between:
        candidate = between.group(1).strip()
        if candidate:
            return _normalise_location(candidate)

    wien = _WIEN_PREFIX_RE.search(haystack)
    if wien:
        suffix = wien.group(1).strip()
        if suffix:
            return f"Wien {suffix}"

    for match in _STATION_NAME_RE.finditer(haystack):
        candidate = _normalise_location(match.group(1))
        if not candidate:
            continue
        first_word = candidate.split(maxsplit=1)[0]
        if first_word in _STOPWORD_LOCATIONS:
            continue
        if len(candidate) < 3:
            continue
        return candidate

    return "unbekannt"


def _normalise_location(value: str) -> str:
    """Trim, collapse whitespace, and cap *value* at a sensible length."""
    cleaned = " ".join(value.split())
    if len(cleaned) > 80:
        cleaned = cleaned[:80].rstrip()
    return cleaned


__all__ = [
    "STAMMSTRECKE_HEADER",
    "STOERUNGEN_HEADER",
    "WEEKDAY_LABELS",
    "DEFAULT_STATS_DIR",
    "VIENNA_TZ",
    "append_stammstrecke_row",
    "append_disruption_row",
    "extract_location_name",
    "stats_path",
    "to_vienna",
]
