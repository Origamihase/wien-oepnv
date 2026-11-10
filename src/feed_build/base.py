"""Shared low-level parts of the feed build: logger, XML namespaces, sanitiser, dates.

The logger keeps the name ``build_feed``. Importing this module registers the
XML namespaces (``ET.register_namespace``), as importing build_feed did.

Moved verbatim from ``src/build_feed.py`` (700-line rule, Etappe 8); ``src.build_feed``
re-exports every name that src/, scripts/ or the tests read from it.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET  # nosec B405
from datetime import datetime, UTC
from email.utils import format_datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

from dateutil import parser

from ..feed import config as feed_config
from ..utils.logging import sanitize_log_arg
from ..utils.text import PLANNED_DISRUPTION_RE
from ..utils.vienna_clock import plausible_moment

__all__ = [
    "ATOM_NS", "GEORSS_NS", "MEDIA_NS", "log", "validate_path", "_VIENNA_TZ", "_SENTENCE_SPLIT_RE", "_to_utc",
    "_fmt_rfc2822", "_PLANNED_DISRUPTION_RE", "_OEBB_SOURCE", "_CONTROL_RE", "_LINE_TOKEN_RE", "_LINE_PREFIX_RE",
    "_WHITESPACE_RE", "_WHITESPACE_CLEANUP_RE", "_sanitize_text", "_ymd_or_none", "_parse_datetime",
    "_coerce_datetime_field", "_normalize_item_datetimes", "_parse_state_time", "_parse_first_seen", "_state_stamp",
]


# Register namespaces globally for thread-safe XML generation
ATOM_NS = "http://www.w3.org/2005/Atom"
ET.register_namespace("atom", ATOM_NS)
ET.register_namespace("ext", "https://wien-oepnv.example/schema")
ET.register_namespace("content", "http://purl.org/rss/1.0/modules/content/")
GEORSS_NS = "http://www.georss.org/georss"
ET.register_namespace("georss", GEORSS_NS)
MEDIA_NS = "http://search.yahoo.com/mrss/"
ET.register_namespace("media", MEDIA_NS)

log = logging.getLogger("build_feed")


# Expose validate_path for tests that patch it or use it
validate_path = feed_config.validate_path

_VIENNA_TZ = ZoneInfo("Europe/Vienna")


# A real sentence end for the summary extract: a period after at least
# FOUR letters — not a digit (``17. Februar``), not a 1–3-letter
# abbreviation (``Gerasdorf b. Wien``, ``Wien Hbf.``, ``Karlsplatz U.``) —
# followed by whitespace and an uppercase letter. Shared by the extract in
# ``_format_item_content`` and by ``_prefer_measures`` below, so the two
# can never disagree on where a sentence ends.
_SENTENCE_SPLIT_RE: re.Pattern[str] = re.compile(
    r"(?<=[A-Za-zÄÖÜäöüß]{4}[.!?])\s+(?=[A-ZÄÖÜ])"
)


# ---------------- Helpers ----------------

def _to_utc(dt: datetime) -> datetime:
    """Return a timezone-aware datetime in UTC.

    Strictly requires timezone-aware datetimes.
    """
    if dt.tzinfo is None:
        raise ValueError("Naive datetimes are banned. Must be timezone-aware.")

    if dt.tzinfo is UTC:
        return dt

    return dt.astimezone(UTC)

def _fmt_rfc2822(dt: datetime) -> str:
    """Format datetime as RFC-2822 string with Vienna offset."""
    if dt.tzinfo is None:
        raise ValueError("Naive datetimes are banned in _fmt_rfc2822.")

    try:
        # Convert to Vienna time for output
        local_dt = _to_utc(dt).astimezone(_VIENNA_TZ)
        return format_datetime(local_dt)
    except Exception:
        log.exception(
            "Konnte Datum %r nicht per format_datetime formatieren – nutze strftime-Fallback.",
            dt,
        )
        try:
            local_dt = dt.astimezone(_VIENNA_TZ)
        except Exception:
            local_dt = dt.astimezone(UTC)

        return local_dt.strftime("%a, %d %b %Y %H:%M:%S %z")


# A disruption that is planned rather than an incident: shared with
# ``wl_resolved`` and described at :data:`src.utils.text.PLANNED_DISRUPTION_RE`.
_PLANNED_DISRUPTION_RE = PLANNED_DISRUPTION_RE


_OEBB_SOURCE = "ÖBB"


# Entfernt XML-unerlaubte Kontrollzeichen (außer \t, \n, \r) PLUS the
# canonical BiDi / zero-width / Unicode line-terminator family that the
# project-wide invisible-character floor strips
# (:data:`src.utils.logging._INVISIBLE_DANGEROUS_RE` /
# :data:`src.utils.text._MARKDOWN_NORMALISE_UNSAFE_RE` /
# :data:`src.utils.stats._CSV_CONTROL_CHARS_RE`).
#
# This regex is the LAST sanitiser before every feed item title /
# description / time-line lands inside the public RSS XML at
# ``docs/feed.xml`` (served from
# ``https://origamihase.github.io/wien-oepnv/feed.xml``). Pre-2026-05-10
# the class covered only ASCII C0 (ex-TAB/LF/CR) + DEL — narrower than
# the canonical Trojan-Source / line-terminator union that the
# BiDi-Mark Drift family (Rounds 2-5)
# consolidated as the project-wide floor. The drift opened a
# *Trojan-Source RSS* primitive on the public feed:
#
#  * **U+202E (RLO)** — a planted upstream title with a U+202E
#    payload survives all three
#    pre-fix defences (``_CONTROL_RE.sub("")`` does not match RLO,
#    ``_WHITESPACE_RE.sub(" ")`` only collapses Unicode whitespace
#    [RLO is not whitespace per :func:`str.isspace`], ``_cdata_content``
#    only escapes ``]]>`` / ElementTree XML escape only handles
#    ``<>&"``). The bytes land verbatim inside ``<title>`` of
#    ``docs/feed.xml`` and Unicode-aware feed readers (Feedly,
#    NetNewsWire, Inoreader, Vivaldi RSS) render the post-RLO segment
#    reversed visually. CVE-2021-42574 shape on a public artefact.
#  * **U+200B-U+200D (ZWSP/ZWNJ/ZWJ) / U+FEFF (BOM)** — invisible byte
#    insertions that hash to a different identity than the same title
#    without them. A hostile upstream can churn the dedup window
#    indefinitely with visually-identical "fresh" items.
#  * **U+2028 / U+2029 (LINE / PARAGRAPH SEPARATOR)** — some Unicode-
#    aware readers honour these as line breaks, splitting one title
#    into multiple visual lines (Feedly mobile honours U+2028 exactly
#    as ``\n``).
#  * **U+0085 (NEL)** + **U+0080-U+0084, U+0086-U+009F (C1 controls)**
#    — record terminators in several SIEM splitters and Markdown
#    consumers downstream from the published feed.
#
# The widening is **additive** — every character the pre-fix regex
# matched still matches post-fix (verified by
# ``test_control_re_preserves_existing_coverage``). TAB (``\x09``),
# LF (``\x0A``), CR (``\x0D``), and SPACE (``\x20``) remain unmatched
# (RSS allows them and the downstream ``_WHITESPACE_RE`` collapse
# normalises them). The Unicode escape form keeps Bandit B613 happy
# and mirrors the regex shape established by the 2026-05-10 CSV writer
# round in :data:`src.utils.stats._CSV_CONTROL_CHARS_RE`.
# 2026-05-11 "Tag-Character / Variation-Selector Drift": widened in
# lockstep with the canonical _INVISIBLE_DANGEROUS_RE union to cover
# the Unicode Tag block (U+E0000..U+E007F), the BMP Variation
# Selectors (U+FE00..U+FE0F), and the supplementary Variation
# Selectors (U+E0100..U+E01EF). Tag bytes survive pre-fix into the
# public RSS XML at docs/feed.xml - ElementTree XML serialisation
# does NOT escape supplementary-plane code points (they are valid
# Unicode characters, not XML metacharacters). The widening at this
# LAST-sanitiser-boundary stops the invisible smuggling primitive
# before it reaches every subscriber's RSS reader.
# 2026-05-14 "Zero-Width Format Drift": widened in lockstep with the
# canonical ``src/utils/logging.py:_INVISIBLE_DANGEROUS_RE`` to cover
# U+180E (MONGOLIAN VOWEL SEPARATOR) and U+2060..U+2064 (WORD JOINER,
# FUNCTION APPLICATION, INVISIBLE TIMES, INVISIBLE SEPARATOR,
# INVISIBLE PLUS). Pre-fix the public RSS feed at docs/feed.xml could
# carry these zero-width Cf primitives inside <title>/<description>/
# <link>; they survive into every subscriber's reader as
# steganography / prompt-injection smuggling primitives. The
# U+2060..U+2069 expansion folds in the existing BiDi-isolate band;
# reserved U+2065 has no defined meaning so the additive strip is
# safe. Inventory invariant pinned by
# ``tests/test_sentinel_zero_width_invisible_drift/``.
# 2026-05-14 "Cf-Format Drift": widened in lockstep with the canonical
# ``src/utils/logging.py:_INVISIBLE_DANGEROUS_RE`` to cover the
# remaining 13 Unicode Cf-class bands (51 code points): U+00AD SOFT
# HYPHEN, U+0600..U+0605 Arabic prefix marks, U+06DD, U+070F,
# U+0890..U+0891, U+08E2, U+206A..U+206F deprecated BiDi controls
# (folds the existing U+2060..U+2069 band into U+2060..U+206F),
# U+FFF9..U+FFFB INTERLINEAR ANNOTATION, U+110BD/U+110CD KAITHI,
# U+13430..U+1343F EGYPTIAN HIEROGLYPH, U+1BCA0..U+1BCA3 SHORTHAND
# FORMAT, and U+1D173..U+1D17A MUSICAL SYMBOL formatting. Pre-fix
# Cf bytes survived into the public RSS feed at docs/feed.xml -
# ElementTree XML serialisation does not escape supplementary-plane
# code points (they are valid Unicode characters, not XML
# metacharacters). SOFT HYPHEN especially renders zero-width
# unconditionally so a planted "Verspaetung<U+00AD>U6 evil"
# title reaches every subscriber's RSS reader visually identical
# to the legitimate text but byte-distinct downstream.
# XML-invalid code points (added 2026-05): ElementTree serialises feed item
# title/description/guid into the public docs/feed.xml + feed.en.xml. Two byte
# shapes from a hostile/garbled upstream value survive the Cf/control set above
# and break the feed (both verified end-to-end through ``_make_rss``):
#   * U+FFFE / U+FFFF \u2014 forbidden by the XML 1.0 Char production
#     ([#x20-#xD7FF] | [#xE000-#xFFFD]); a planted title yields a feed that
#     fails to parse (ParseError: not well-formed) in every subscriber's reader.
#   * U+D800-U+DFFF surrogates \u2014 a lone surrogate (reachable via a ``\uD800``
#     escape in upstream JSON) raises UnicodeEncodeError at serialisation and
#     aborts the WHOLE build.
# NOTE: the noncharacters U+FDD0-U+FDEF and supplementary-plane U+nFFFE/U+nFFFF
# are deliberately NOT stripped \u2014 they are *valid* per the XML 1.0 Char grammar
# (confirmed: they round-trip through ElementTree), so removing them would be
# silent data loss, not a fix.
_CONTROL_RE = re.compile(
    r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x9F"
    r"\u00ad\u0600-\u0605\u061c\u06dd\u070f\u0890\u0891\u08e2\u180e"
    r"\u200b-\u200f\u2028-\u202e\u2060-\u206f\ufeff"
    r"\ud800-\udfff\ufe00-\ufe0f\ufff9-\ufffb\ufffe\uffff"
    r"\U000110bd\U000110cd"
    r"\U00013430-\U0001343F"
    r"\U0001bca0-\U0001bca3"
    r"\U0001d173-\U0001d17a"
    r"\U000e0000-\U000e007f\U000e0100-\U000e01ef]"
)

# Prefix pattern for line identifiers like "U1/U2: "
_LINE_TOKEN_RE = re.compile(r"^(?:\d{1,3}[A-Z]?|[A-Z]{1,4}\d{0,3})$")

_LINE_PREFIX_RE = re.compile(
    r"^\s*([A-Za-z0-9]+\s*(?:/\s*[A-Za-z0-9]+){0,20})\s*:\s*"
)

# _WHITESPACE_RE captures all whitespace including newlines (\n).
# _WHITESPACE_CLEANUP_RE only matches horizontal whitespace (spaces, tabs, etc.) to preserve intended line breaks.
_WHITESPACE_RE = re.compile(r"\s+")
_WHITESPACE_CLEANUP_RE = re.compile(r"[ \t\r\f\v]+")

# The line-break members of ``_CONTROL_RE`` (vertical tab, form feed,
# NEXT LINE, line / paragraph separator) are line breaks in upstream text.
# Deleting them glues two words into one, so they become a space first —
# see ``_SEPARATOR_PRIMITIVES_RE`` in :mod:`src.utils.serialize`.
_SEPARATOR_CONTROL_RE = re.compile(r"[\x0b\x0c\x85\u2028\u2029]")


def _sanitize_text(s: str) -> str:
    return _CONTROL_RE.sub("", _SEPARATOR_CONTROL_RE.sub(" ", s or ""))


def _ymd_or_none(dt: datetime | None) -> str:
    """The UTC calendar day of *dt* as ``YYYY-MM-DD``; ``""`` without a time.

    Used only by the fallback identity of :func:`_identity_for_item` for an
    item without a provider ``_identity``. Every current provider also sets a
    ``guid``, which ``_dedupe_key_for_item`` and ``_state_key_for_item``
    prefer, so no running item and no ``first_seen`` depends on this day; the
    Wiener Linien identity (``_wl_identity``) takes the Vienna day instead.
    ``tests/test_identity_utc_day.py`` pins the UTC day of this fallback so
    that a change is a decision, not an accident (code audit 2026-10-31).
    """
    if isinstance(dt, datetime):
        return _to_utc(dt).date().isoformat()
    return ""


def _parse_datetime(value: Any) -> datetime | None:
    """Parse ISO8601 timestamps (incl. ``Z`` suffix and compact offsets).

    Treats naive timestamps as Vienna local time. A year outside
    :data:`src.utils.vienna_clock.PLAUSIBLE_YEARS` counts as not set
    (``None``): the chain adds to these values and moves them between time
    zones, which a date at the edge of the calendar cannot take.
    """

    if isinstance(value, datetime):
        if plausible_moment(value) is None:
            return None
        if value.tzinfo:
            return value
        return value.replace(tzinfo=_VIENNA_TZ)

    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None

        try:
            parsed = parser.isoparse(candidate)
            if plausible_moment(parsed) is None:
                return None
            if parsed.tzinfo is None:
                # Assume Vienna time for naive strings (e.g. from legacy cache)
                parsed = parsed.replace(tzinfo=_VIENNA_TZ)
            return cast('datetime | None', parsed)
        except (ValueError, OverflowError, parser.ParserError) as exc:
            # Security (Clear-Text-Logging Drift): ``parser.ParserError``
            # embeds the original input string in its message —
            # upstream-controlled bytes flow into the log line via
            # ``str(exc)``.  ``%r`` already escapes ``value`` itself.
            log.debug(
                "Datetime-Parsing fehlgeschlagen für %r (%s)",
                value,
                sanitize_log_arg(str(exc)),
            )

    return None


def _coerce_datetime_field(it: dict[str, Any], field: str) -> datetime | None:
    value = it.get(field)
    if value is None:
        return None

    parsed = _parse_datetime(value)
    if parsed is None:
        if isinstance(value, str):
            log.warning("%s Parsefehler: %r", field, value)
        it[field] = None
        return None

    it[field] = parsed
    return parsed


def _normalize_item_datetimes(
    items: list[Any],
    fields: tuple[str, ...] = ("pubDate", "starts_at", "ends_at"),
) -> list[Any]:
    for item in items:
        if not isinstance(item, dict):
            continue
        for field_name in fields:
            _coerce_datetime_field(item, field_name)
    return items


def _state_stamp(raw: object) -> datetime | None:
    """The time stamp *raw* of the state file, as written.

    ``None`` when it cannot be read or its year, as written, lies outside
    :data:`src.utils.vienna_clock.PLAUSIBLE_YEARS`. The state is written by
    the build itself, always in UTC; a value nobody can compute with must
    not stop the build. (A value written with another offset within hours of
    a boundary is read here and can leave the range once the loader has
    stored it in UTC; the next reader then takes it as unreadable.)
    """
    try:
        return plausible_moment(datetime.fromisoformat(str(raw)))
    except ValueError:
        return None


def _parse_state_time(entry: dict[str, Any] | None, field: str) -> datetime | None:
    """Return the UTC datetime stored under ``field`` of a state ``entry``.

    ``None`` when the entry is absent or the field is missing, unparseable
    or outside the plausible years (:func:`_state_stamp`).
    """
    if not entry:
        return None
    raw = entry.get(field)
    parsed = None if raw is None else _state_stamp(raw)
    if parsed is None:
        return None
    return _to_utc(parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC))


def _parse_first_seen(
    entry: dict[str, Any] | None, fallback: datetime | None
) -> datetime | None:
    """Return the UTC ``first_seen`` datetime from a state ``entry``.

    Falls back to ``fallback`` when the entry is absent or its ``first_seen``
    is missing/unparseable.
    """
    parsed = _parse_state_time(entry, "first_seen")
    return fallback if parsed is None else parsed
