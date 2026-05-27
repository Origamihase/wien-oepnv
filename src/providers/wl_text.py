"""Utility functions for Wiener Linien text handling and filtering."""

from __future__ import annotations

import re
from datetime import date, datetime, UTC
from zoneinfo import ZoneInfo

_VIENNA_TZ = ZoneInfo("Europe/Vienna")

# ---------------- Relevanz-/Ausschluss-Filter ----------------

KW_RESTRICTION = re.compile(
    r"""
    \b(
        umleitung       # detour
        | ersatzverkehr  # replacement service
        | unterbrech     # interruption
        | sperr          # closure prefix
        | gesperrt       # blocked
        | störung        # disruption (umlaut)
        | stoerung       # disruption (oe)
        | arbeiten       # works
        | baustell       # construction site
        | einschränk     # restriction (umlaut)
        | verspät        # delay
        | ausfall        # outage
        | verkehr        # traffic
        | kurzführung    # short service (umlaut)
        | kurzfuehrung   # short service (ue)
        | teilbetrieb    # partial service
        | pendelverkehr  # shuttle service
        | kurzstrecke    # short route
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

KW_EXCLUDE = re.compile(
    r"\b(willkommen|gewinnspiel|anzeiger|eröffnung|eroeffnung|service(?:-info)?|info(?:rmation)?|fest|keine\s+echtzeitinfo)\b",
    re.IGNORECASE,
)

FACILITY_ONLY = re.compile(
    # Match the facility root anywhere inside a German compound noun
    # (``Personenlift``, ``Aufzugsanlage``, ``Liftbetrieb`` etc.) —
    # the bare-root pattern with ``\b`` on both sides misses real
    # ÖBB titles like ``Technische Störung des Personenlift``.
    r"\b\w*(?:aufzug|aufz(?:ü|ue)ge|lift|fahrstuhl|"
    r"fahrtreppen?(?:info)?|rolltreppen?|aufzugsinfo)\w*\b",
    re.IGNORECASE,
)


def _is_facility_only(*texts: str) -> bool:
    """Return ``True`` if the combined text refers only to facilities."""

    t = " ".join([x for x in texts if x]).lower()
    if len(t) > 500:
        t = t[:500]
    return bool(FACILITY_ONLY.search(t))


# ---------------- Titel-Kosmetik ----------------

_LABELS = [
    r"bauarbeiten",
    r"straßenbauarbeiten",
    r"strassenbauarbeiten",
    r"gleisbauarbeiten",
    r"verkehrsinfo",
    r"verkehrsinformation",
    r"verkehrsmeldung",
    r"störung",
    r"stoerung",
    r"hinweis",
    r"serviceinfo",
    r"service\-info",
    r"information",
]
_LABEL_HEAD_RE = re.compile(
    r"^\s*(?:(?:" + "|".join(_LABELS) + r")\s*(?:[-:–—/]\s*|\s+))+",
    re.IGNORECASE,
)


def _is_informative(rest: str) -> bool:
    return bool(rest and re.search(r"[A-Za-zÄÖÜäöüß0-9]{3,}", rest))


def _tidy_title_wl(title: str) -> str:
    """Entfernt generische Label am Anfang, wenn danach informativer Text steht.

    Real WL feed titles sometimes carry embedded newlines (``"Ersatzbus
    41E\\nhält beim 10A"``) that survived the previous ``\\s{2,}``
    collapse — a single newline matches ``\\s`` exactly once and so was
    preserved verbatim. RSS/Atom titles are single-line by convention,
    so we now collapse ANY whitespace run (including isolated newlines
    or tabs) to a single space.
    """

    t = (title or "").strip()
    if len(t) > 500:
        t = t[:500]
    if not t:
        return t
    stripped = _LABEL_HEAD_RE.sub("", t)
    if stripped and _is_informative(stripped):
        t = stripped
    t = re.sub(r"[<>«»‹›]+", "", t)  # spitze Klammern/Anführungen
    t = re.sub(r"\s+ab\s+\d{1,2}\.\d{1,2}\.(?:\d{4})?", "", t, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", t).strip(" -–—:/\t")


# ---------------- Datum aus Titel extrahieren ----------------

# German month names incl. the Austrian variants ``Jänner`` (Januar)
# and ``Feber`` (Februar) — both occur in real Wiener-Linien titles.
_MONTHS_DE: dict[str, int] = {
    "jänner": 1,
    "januar": 1,
    "februar": 2,
    "feber": 2,
    "märz": 3,
    "april": 4,
    "mai": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "dezember": 12,
}

# ``ab DD.MM.[YYYY]`` — numeric form (trailing dot after the month is
# mandatory, matching the legacy pattern).
_DATE_NUMERIC_RE = re.compile(
    r"ab\s+(\d{1,2})\.(\d{1,2})\.(\d{4})?",
    re.IGNORECASE,
)
# ``ab DD. <Monat> [YYYY]`` — spelled-out form WL uses for advance
# notices (e.g. ``ab 07. April 2026``). The legacy code ignored this
# shape entirely, so those start dates silently fell back to the API
# publication date.
_DATE_MONTHNAME_RE = re.compile(
    r"ab\s+(\d{1,2})\.?\s*("
    + "|".join(re.escape(m) for m in _MONTHS_DE)
    + r")\b(?:\s+(\d{4}))?",
    re.IGNORECASE | re.UNICODE,
)


def _extract_day_month_year(title: str) -> tuple[int, int, int | None] | None:
    """Return ``(day, month, year_or_None)`` from the first ``ab`` date.

    Tries the numeric form first, then the spelled-out month form. The
    two patterns are mutually exclusive, so order only decides which
    wins when a title (pathologically) carries both.
    """
    match = _DATE_NUMERIC_RE.search(title)
    if match:
        day_str, month_str, year_str = match.groups()
        return int(day_str), int(month_str), int(year_str) if year_str else None
    match = _DATE_MONTHNAME_RE.search(title)
    if match:
        day_str, month_word, year_str = match.groups()
        return (
            int(day_str),
            _MONTHS_DE[month_word.lower()],
            int(year_str) if year_str else None,
        )
    return None


def _resolve_missing_year(month: int, day: int, reference_date: datetime) -> int | None:
    """Resolve a missing year to the ``DD.MM`` occurrence nearest *reference_date*.

    WL omits the year only around the turn of the year, so picking the
    occurrence closest to the publication/validity reference resolves
    the Dec->Jan rollover without a magic past-window constant. Ties
    favour the future occurrence (``ab`` denotes a start, i.e. an
    advance notice). Crucially, the callsite only *applies* the result
    when it is strictly later than the API start, so a date resolved
    into the recent past is safely discarded in favour of the reliable
    API start rather than being fabricated a year ahead.

    Returns ``None`` when ``DD.MM`` is invalid in every candidate year
    (e.g. ``29.02`` with no nearby leap year).
    """
    if reference_date.tzinfo is None:
        reference_date = reference_date.replace(tzinfo=UTC)
    ref_date = reference_date.astimezone(_VIENNA_TZ).date()
    best_key: tuple[int, int] | None = None
    best_year: int | None = None
    for year in (ref_date.year - 1, ref_date.year, ref_date.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        key = (abs((candidate - ref_date).days), -year)
        if best_key is None or key < best_key:
            best_key, best_year = key, year
    return best_year


def extract_date_from_title(
    title: str, reference_date: datetime | None = None
) -> datetime | None:
    """Extract an ``ab <Datum>`` start date from a Wiener-Linien title.

    Recognises both the numeric ``ab DD.MM.[YYYY]`` and the spelled-out
    ``ab DD. <Monat> [YYYY]`` forms (incl. the Austrian ``Jänner`` /
    ``Feber``). A missing year is resolved against *reference_date*
    (default: now in UTC) via :func:`_resolve_missing_year`. Returns a
    midnight Europe/Vienna datetime, or ``None`` when no parseable date
    is present.
    """
    if not title:
        return None
    if len(title) > 500:
        title = title[:500]

    parsed = _extract_day_month_year(title)
    if parsed is None:
        return None
    day, month, year = parsed

    if year is None:
        if reference_date is None:
            reference_date = datetime.now(UTC)
        year = _resolve_missing_year(month, day, reference_date)
        if year is None:
            return None

    try:
        return datetime(year, month, day, tzinfo=_VIENNA_TZ)
    except ValueError:
        return None


# ---------------- „Kernbegriff/Topic“ für Dedupe ----------------

TITLE_TOPIC_TOKENS = {
    "falschparker",
    "polizeieinsatz",
    "rettungseinsatz",
    "unfall",
    "signalstörung",
    "signalstoerung",
    "umleitung",
    "ersatzverkehr",
    "kurzführung",
    "kurzfuehrung",
    "sperre",
    "gesperrt",
}

_GENERIC_FILLER = re.compile(
    r"\b(fahrtbehinderung|verkehrsbehinderung|behinderung|störung|stoerung|hinweis|meldung|serviceinfo|service\-info|"
    r"betrieb\s+ab.*|betrieb\s+nur.*)\b",
    re.IGNORECASE,
)


def _title_core(t: str) -> str:
    t2 = _tidy_title_wl(t)
    if len(t2) > 500:
        t2 = t2[:500]
    t2 = re.sub(r"[^\wäöüÄÖÜß]+", " ", t2, flags=re.UNICODE)
    t2 = re.sub(r"\s{2,}", " ", t2).strip().casefold()
    return t2


def _topic_key_from_title(raw: str) -> str:
    if raw and len(raw) > 500:
        raw = raw[:500]
    t = _GENERIC_FILLER.sub(" ", raw or "")
    t = re.sub(r"[^\wäöüÄÖÜß]+", " ", t, flags=re.UNICODE).casefold()
    toks = {w for w in t.split() if w in TITLE_TOPIC_TOKENS}
    if toks:
        return " ".join(sorted(toks))
    return _title_core(raw)


__all__ = [
    "KW_RESTRICTION",
    "KW_EXCLUDE",
    "_is_facility_only",
    "_tidy_title_wl",
    "_title_core",
    "_topic_key_from_title",
    "extract_date_from_title",
]
