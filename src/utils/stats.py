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
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

from src.utils.files import read_capped_text
from src.utils.logging import sanitize_log_arg
from src.utils.stations import display_name, station_info

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

# Heuristic field-extraction patterns. All patterns operate on the
# already-sanitised plain-text title/description coming out of the
# providers — no HTML, no control bytes, no upstream-controlled
# unbounded length (the providers cap titles at ~200 chars and
# descriptions at a few hundred). The patterns are deliberately broad-
# but-bounded: ``{2,80}`` upper bounds keep ReDoS off the table even on
# pathological input.
# CSV formula-injection (CWE-1236, OWASP "CSV Injection") defence.
#
# Excel, LibreOffice Calc, and Google Sheets evaluate any cell whose
# content begins with one of these characters as a *formula* on file
# open. The append-only stats writers persist three operator-/upstream-
# influenced text fields (``provider``, ``location_name`` for the
# disruption ledger; ``direction`` for the Stammstrecke ledger) — each
# is the boundary where defence-in-depth must clamp a payload that
# could otherwise have been planted via a poisoned cache file
# (``cache/wl/*.json`` re-emits ``ev["source"]`` verbatim into
# ``provider`` — see ``src/providers/wl_fetch.py`` lines 736 and 858),
# a poisoned station directory (``data/stations.json`` flows through
# ``display_name`` into ``direction``), or a future loosening of
# :func:`extract_location_name`'s directory-anchored gate. The
# OWASP-recommended neutralisation is a leading single quote ``'``,
# which spreadsheets render as plain text (the apostrophe itself is
# hidden in display) — defanged but still visible to operators
# scanning the CSV for indicators of compromise.
_CSV_FORMULA_PREFIXES: Final = ("=", "+", "-", "@", "\t", "\r")
# C0/C1 control-byte + BiDi / zero-width / line-terminator stripper.
#
# The character class union mirrors the canonical sanitiser
# :data:`src.utils.text._MARKDOWN_NORMALISE_UNSAFE_RE` (and its parent
# :data:`src.utils.logging._INVISIBLE_DANGEROUS_RE`) so the four
# orthogonal threat classes are closed at the CSV write boundary too:
#
# 1. **C0 controls** (``\x00-\x08`` + ``\x0B-\x0C`` + ``\x0E-\x1F``)
#    plus DEL (``\x7F``). NUL silently truncates fields in some
#    downstream CSV reader variants; BEL / VT / FF / SI / SO mangle
#    operator-facing terminal output. Excludes TAB (``\x09``), LF
#    (``\x0A``), and CR (``\x0D``) from the body since :mod:`csv`
#    already QUOTE_MINIMAL-wraps fields containing newlines and
#    embedded TAB is benign for the default ``,`` delimiter — *leading*
#    TAB / CR are still defanged by the formula-prefix branch below.
# 2. **C1 controls** (``\x7F-\x9F``). U+0085 NEXT LINE in particular is
#    treated as a record terminator by several CSV / SIEM splitters
#    (and by some Excel locales), splitting a single cell into multiple
#    rows downstream — same exfiltration shape as an embedded newline.
# 3. **BiDi format controls** (U+061C ALM, U+202A-U+202E
#    LRE/RLE/PDF/LRO/**RLO**, U+2066-U+2069 LRI/RLI/FSI/PDI). These are
#    the canonical CVE-2021-42574 Trojan-Source primitives. Stripping
#    them at the writer also closes a *formula-injection bypass*:
#    leading invisible characters (``<U+200B>=cmd…`` /
#    ``<U+202E>=cmd…``) survive ``str.strip()`` (they are not whitespace per
#    :func:`str.isspace`), so the downstream
#    ``cleaned.startswith(_CSV_FORMULA_PREFIXES)`` check returns
#    ``False`` and the apostrophe-defang is never applied. Excel /
#    LibreOffice Calc / Google Sheets render the cell with the
#    invisible prefix collapsed and may still evaluate the residual
#    ``=cmd…`` as a formula. The fix shape — strip the invisible
#    prefix at sanitiser entry — re-aligns the formula-prefix check
#    with the visible cell content.
# 4. **Zero-width characters** (U+200B-U+200F ZWSP/ZWNJ/ZWJ + LRM/RLM,
#    U+FEFF BOM). Same formula-injection bypass surface as the BiDi
#    formatting controls; LRM/RLM are full BiDi primitives despite
#    being zero-width.
#
# The pre-fix regex covered only (1); the BiDi-Mark Drift family
# (Rounds 2-4) widened ``_CONTROL_CHARS_RE``,
# ``_INVISIBLE_DANGEROUS_RE``, ``_MARKDOWN_NORMALISE_UNSAFE_RE``,
# ``_UNSAFE_CHARS_RE``, and ``_UNSAFE_URL_CHARS`` to cover (2)-(4) but
# explicitly deferred this CSV writer's regex. The inventory test
# ``test_csv_control_chars_regex_covers_canonical_invisible_dangerous_set``
# in ``tests/test_sentinel_csv_formula_injection_invisible_prefix.py``
# pins the invariant programmatically — any future widening of the
# canonical log-sanitiser fails the test until this regex widens too.
# 2026-05-11 "Tag-Character / Variation-Selector Drift": widened in
# lockstep with the canonical ``_INVISIBLE_DANGEROUS_RE`` union to
# cover the Unicode Tag block (U+E0000..U+E007F), the BMP Variation
# Selectors (U+FE00..U+FE0F), and the supplementary Variation
# Selectors (U+E0100..U+E01EF). Tag-character variants of a provider
# / location name silently fracture downstream pivot-table analytics:
# Excel / LibreOffice Calc / Google Sheets render the invisible-tag
# variant as visually-identical text but aggregate it as a distinct
# cell from the visible cousin. Closing the variant gap at the CSV
# writer keeps the operator's analytics view consistent with the
# rendered cells.
# 2026-05-14 "Zero-Width Format Drift": widened in lockstep with the
# canonical _INVISIBLE_DANGEROUS_RE union to cover U+180E (MONGOLIAN
# VOWEL SEPARATOR) and U+2060..U+2064 (WORD JOINER, FUNCTION
# APPLICATION, INVISIBLE TIMES, INVISIBLE SEPARATOR, INVISIBLE PLUS).
# Pre-fix invisible-Format variants of a provider/location name
# silently fractured pivot-table aggregation in the committed CSV
# stats ledger (Excel/LibreOffice/Sheets render the bytes as zero
# width, so operators see two visually identical cells aggregating
# as distinct keys). The U+2060..U+2069 expansion folds the existing
# BiDi-isolate band into the new range; reserved U+2065 has no
# defined meaning so the additive strip is safe.
# 2026-05-14 "Cf-Format Drift": widened in lockstep with the canonical
# _INVISIBLE_DANGEROUS_RE union to cover the remaining 13 Unicode
# Cf-class bands (44 code points): U+00AD SOFT HYPHEN, U+0600..U+0605
# Arabic prefix marks, U+06DD, U+070F, U+0890..U+0891, U+08E2,
# U+206A..U+206F deprecated BiDi controls (folds the existing
# U+2060..U+2069 band into U+2060..U+206F), U+FFF9..U+FFFB INTERLINEAR
# ANNOTATION, U+110BD/U+110CD KAITHI, U+13430..U+13438 EGYPTIAN
# HIEROGLYPH, U+1BCA0..U+1BCA3 SHORTHAND FORMAT, and U+1D173..U+1D17A
# MUSICAL SYMBOL formatting. Pre-fix Cf-class invisible variants of
# a provider/location name silently fractured pivot-table aggregation
# (Excel/LibreOffice/Sheets render Cf bytes as zero width). SOFT
# HYPHEN especially is the canonical "invisible-by-default" character
# used in real-world dedup-key spoofing attacks since 2018.
_CSV_CONTROL_CHARS_RE: Final = re.compile(
    r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x9F"
    r"\u00ad\u0600-\u0605\u061c\u06dd\u070f\u0890\u0891\u08e2\u180e"
    r"\u200b-\u200f\u2028-\u202e\u2060-\u206f"
    r"\ufe00-\ufe0f\ufeff\ufff9-\ufffb"
    r"\U000110bd\U000110cd"
    r"\U00013430-\U00013438"
    r"\U0001bca0-\U0001bca3"
    r"\U0001d173-\U0001d17a"
    r"\U000e0000-\U000e007f\U000e0100-\U000e01ef]"
)
# Hard cap on persisted text-field length. Generous enough that any
# legitimate provider/location/direction string survives untouched;
# tight enough that an attacker who lands an unbounded string into one
# of the upstream fields cannot inflate ``data/stats/*.csv`` past a
# predictable per-row footprint. The location-name heuristic already
# caps at 80 in :func:`_normalise_location`; this is a second-layer
# clamp at the CSV boundary itself.
_CSV_TEXT_FIELD_MAX_LEN: Final = 200


def _sanitize_csv_text_field(value: str) -> str:
    """Neutralise spreadsheet formula injection in *value*.

    Pipeline:

    1. Strip control characters (the regex preserves embedded TAB / LF /
       CR — :mod:`csv` already QUOTE_MINIMAL-wraps newlines, embedded
       TAB is benign for the default ``,`` delimiter, *leading* TAB / CR
       are still defanged in step 4).
    2. Strip leading/trailing whitespace. Performed *before* the
       formula-prefix check so a payload like ``"   =cmd"`` (leading
       whitespace as a known evasion vector — some CSV consumers and
       spreadsheet importers trim whitespace before evaluating) cannot
       slip past the prefix branch and then have its whitespace
       collapsed by a downstream ``.strip()``.
    3. Cap length at :data:`_CSV_TEXT_FIELD_MAX_LEN` (defends against
       an unbounded operator/upstream string inflating ``data/stats``).
    4. Prepend a single quote (``'``) to any value beginning with one
       of :data:`_CSV_FORMULA_PREFIXES`. The leading apostrophe is
       hidden in spreadsheet display but forces the cell to be parsed
       as text; operators scanning the raw CSV still see the (defanged)
       payload, which preserves the indicator-of-compromise signal.
    """
    cleaned = _CSV_CONTROL_CHARS_RE.sub("", value).strip()
    if len(cleaned) > _CSV_TEXT_FIELD_MAX_LEN:
        cleaned = cleaned[:_CSV_TEXT_FIELD_MAX_LEN].rstrip()
    if cleaned.startswith(_CSV_FORMULA_PREFIXES):
        return "'" + cleaned
    return cleaned


# Structured-signal patterns. Each captures a *labelled* location segment
# that the providers already serialise into ``description`` (or ``title``)
# in a deterministic shape — these are the cleanest signals because the
# field came out of a curated upstream API field (WL ``relatedStops`` /
# attrs.station / attrs.location) or out of our own VOR ``in Richtung``
# render. The capture group is then passed through
# :func:`_resolve_via_directory`, which only returns a value when
# :func:`station_info` recognises it — so a poisoned upstream string can
# never silently land as a "location" cell on the dashboard.
#
# All upper bounds are explicit (``{1,200}`` for the labelled-segment
# captures, ``{1,80}`` for the free-text scans) to keep the regex engine
# bounded on adversarial inputs (ReDoS hardening); the captured value is
# later clamped to 80 chars in :func:`_normalise_location`.
_HALTESTELLE_RE: Final = re.compile(
    r"\|\s*Haltestelle:\s*([^|]{1,200})",
    re.IGNORECASE,
)
_LABELED_LOCATION_RE: Final = re.compile(
    r"\|\s*(?:Station|Location)\s*:\s*([^|]{1,200})",
    re.IGNORECASE,
)
_RICHTUNG_RE: Final = re.compile(
    r"\bin\s+Richtung\s+([^,\.\[\|]{1,80})",
    re.IGNORECASE,
)
_BETWEEN_RE: Final = re.compile(
    r"\bzwischen\s+([A-ZÄÖÜ][\w\.\-’']{1,80}?(?:\s+[A-ZÄÖÜ][\w\.\-’']{1,80}){0,3})"
    r"\s+und\s+",
    re.IGNORECASE,
)
_WIEN_PREFIX_RE: Final = re.compile(
    r"\bWien\s+([A-ZÄÖÜ][\wäöüÄÖÜß\-’']{1,40}(?:\s+[A-ZÄÖÜ][\wäöüÄÖÜß\-’']{1,40})?)"
)

# Sliding-window scan parameters. ``_MAX_STATION_WINDOW`` mirrors the
# value used in :mod:`src.providers.oebb._find_stations_in_text` so the
# two scans behave consistently — a station name that resolves there
# also resolves here. The token-split character class strips arrow /
# slash / dash punctuation that appears in route titles
# (``"A ↔ B"`` / ``"A / B"``) and would otherwise corrupt window joins.
_MAX_STATION_WINDOW: Final = 4
_TOKEN_SPLIT_RE: Final = re.compile(r"[\s/]+")
_NOISE_TOKEN_RE: Final = re.compile(r"^[↔→←↗↘↙↖<>=–—\-«»‹›]+$")
# Single-token chunks that the directory's alias-expansion rules would
# silently collapse into flagship stations. ``Hbf`` aliases to
# ``Wien Hauptbahnhof`` even when the surrounding text is talking about
# a different station ("Verbindungen zum Hbf umgeleitet"); ``Bahnhof`` /
# ``Bf`` likewise; ``Wien`` alone aliases on its own (and would land
# every Vienna-related disruption under the same node). Mirrors the
# ``oebb._GENERIC_STATION_TOKENS`` filter to keep the two scans in sync.
_GENERIC_DIRECTORY_TOKENS: Final = frozenset(
    {
        "hbf",
        "bhf",
        "bf",
        "bahnhof",
        "bahnhst",
        "hauptbahnhof",
        "westbahnhof",
        "westbf",
        "ostbahnhof",
        "ostbf",
        "südbahnhof",
        "suedbahnhof",
        "südbf",
        "suedbf",
        "nordbahnhof",
        "nordbf",
        "station",
        "wien",
        "vor",
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


def _normalise_location(value: str) -> str:
    """Trim, collapse whitespace, and cap *value* at a sensible length."""
    cleaned = " ".join(value.split())
    if len(cleaned) > 80:
        cleaned = cleaned[:80].rstrip()
    return cleaned


def _resolve_via_directory(candidate: str) -> str | None:
    """Return the *display* name when *candidate* resolves via the station
    directory, ``None`` otherwise.

    The directory in ``data/stations.json`` is the project's only
    curated source of truth for "is this a real Vienna-network
    station?". A regex-extracted candidate is only accepted as a
    location when :func:`station_info` recognises it (canonical or
    alias). The returned string is the user-facing
    :func:`display_name` (e.g. ``Wien Mitte`` rather than the
    canonical ``Wien Mitte-Landstraße``) so the dashboard renders the
    label operators expect to read.
    """
    cleaned = _normalise_location(candidate)
    if not cleaned:
        return None
    info = station_info(cleaned)
    if info is None:
        return None
    return display_name(info.name)


def _scan_for_directory_station(haystack: str) -> str | None:
    """Sliding-window scan for a directory-known station in *haystack*.

    Matches longest first within each starting position so that a
    multi-token name (``Wien Mitte``) wins over the substring match
    (``Mitte``) when both resolve, and returns the *first* match in
    source order so the natural reading order of the title/description
    drives the choice. Mirrors
    :func:`src.providers.oebb._find_stations_in_text` semantically but
    is purposefully scoped to the stats-extraction surface (no HTML
    stripping — the callers already pass plain text).

    ``_GENERIC_DIRECTORY_TOKENS`` filters single-token chunks that the
    directory's alias rules silently collapse to flagship stations
    (``Hbf`` → ``Wien Hauptbahnhof`` etc.) — without that filter, every
    ÖBB description that mentions ``Hbf`` would land all incidents under
    a single node and skew the dashboard.
    """
    if not haystack:
        return None
    tokens = [t for t in _TOKEN_SPLIT_RE.split(haystack) if t]
    tokens = [t for t in tokens if not _NOISE_TOKEN_RE.match(t)]
    if not tokens:
        return None
    n = len(tokens)
    window = min(_MAX_STATION_WINDOW, n)
    for start in range(n):
        max_size = min(window, n - start)
        for size in range(max_size, 0, -1):
            chunk_tokens = tokens[start : start + size]
            chunk = " ".join(chunk_tokens)
            if size == 1:
                token_norm = chunk.casefold().rstrip(".:,;")
                if token_norm in _GENERIC_DIRECTORY_TOKENS:
                    continue
                # Drop ultra-short chunks ("S1") to avoid line-token
                # alias collisions; mirrors the 3-letter floor in the
                # ÖBB scan.
                alpha = re.sub(r"[^A-Za-zÄÖÜäöüß]", "", chunk)
                if len(alpha) < 3:
                    continue
            resolved = _resolve_via_directory(chunk.rstrip(".:,;"))
            if resolved:
                return resolved
    return None


def extract_location_name(item: dict[str, Any]) -> str:
    """Pick a directory-validated location for *item* or ``"unbekannt"``.

    The strategy is **catalogue-first**: a candidate string is only
    returned when it resolves through :func:`station_info` (the curated
    directory in ``data/stations.json``). Free-form regex matches over
    capitalised tokens were dropped in 2026-05-09 because German nouns
    are all capitalised — the heuristic accepted disruption types like
    ``Demonstration Linie`` / ``Rettungseinsatz Linie`` /
    ``Polizeieinsatz Linie`` as if they were station names, polluting
    the README "Häufigste Störungsorte" table with non-locations.

    Pipeline (return on first hit):

    1. ``" | Haltestelle: ..."`` — WL provider serialises the
       ``relatedStops`` API field into the description with this
       prefix; the first comma-separated entry is the primary stop.
       When the WL stop name is not in the heavy-rail directory we
       still accept it verbatim (subject to the 80-char clamp) because
       the stop list comes from a curated upstream source.
    2. ``" | Station: ..."`` / ``" | Location: ..."`` — WL extras
       fallback (also curated upstream); only accepted when it
       resolves through the directory.
    3. ``"in Richtung X"`` — the Stammstrecke renderer in
       ``scripts/update_stammstrecke_status.py``; only accepted when
       it resolves through the directory.
    4. ``"zwischen X und Y"`` — canonical ÖBB phrasing; only accepted
       when X resolves through the directory.
    5. ``"Wien <Stadtteil>"`` — common in ÖBB / VOR descriptions; only
       accepted when ``"Wien <Stadtteil>"`` resolves through the
       directory.
    6. Sliding-window directory scan over the title + description.
       Filters single-token generic aliases (``Hbf`` etc.) so
       arbitrary mentions of those words do not auto-canonicalise to
       flagship stations.
    7. Final fallback: ``"unbekannt"``. We deliberately do NOT
       fall back to a free-form regex match here — the previous fix
       round demonstrated that any such fallback re-pollutes the
       statistics ledger.

    The function never raises — a malformed item just returns the
    fallback string.
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

    halt = _HALTESTELLE_RE.search(haystack)
    if halt:
        first_stop = halt.group(1).split(",")[0].strip()
        resolved = _resolve_via_directory(first_stop)
        if resolved:
            return resolved
        normalised = _normalise_location(first_stop)
        if normalised:
            return normalised

    labeled = _LABELED_LOCATION_RE.search(haystack)
    if labeled:
        first_label = labeled.group(1).split(",")[0].strip()
        resolved = _resolve_via_directory(first_label)
        if resolved:
            return resolved

    richtung = _RICHTUNG_RE.search(haystack)
    if richtung:
        target = richtung.group(1).strip()
        resolved = _resolve_via_directory(target)
        if resolved:
            return resolved

    between = _BETWEEN_RE.search(haystack)
    if between:
        endpoint = _normalise_location(between.group(1).strip())
        resolved = _resolve_via_directory(endpoint)
        if resolved:
            return resolved

    wien = _WIEN_PREFIX_RE.search(haystack)
    if wien:
        suffix = wien.group(1).strip()
        if suffix:
            resolved = _resolve_via_directory(f"Wien {suffix}")
            if resolved:
                return resolved

    scan = _scan_for_directory_station(haystack)
    if scan:
        return scan

    return "unbekannt"


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
    cutoff = now - window
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
        for row in reader:
            parsed = _parse_stammstrecke_row(row)
            if parsed is None:
                continue
            if parsed.timestamp < cutoff:
                continue
            observations.append(parsed)
    observations.sort(key=lambda obs: obs.timestamp)
    return observations


__all__ = [
    "AUSFAELLE_HEADER",
    "STAMMSTRECKE_HEADER",
    "STOERUNGEN_HEADER",
    "WEEKDAY_LABELS",
    "DEFAULT_STATS_DIR",
    "MAX_STAMMSTRECKE_CSV_BYTES",
    "StammstreckeObservation",
    "VIENNA_TZ",
    "append_ausfall_row",
    "append_stammstrecke_row",
    "append_disruption_row",
    "extract_location_name",
    "read_recent_stammstrecke_observations",
    "stats_path",
    "to_vienna",
]
