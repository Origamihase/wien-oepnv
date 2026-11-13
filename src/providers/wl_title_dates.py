"""The start date a Wiener Linien title names ("ab 14.09.2026", "am 4. Oktober")."""

from __future__ import annotations

import re
from datetime import date, datetime, UTC
from ..utils.vienna_clock import VIENNA_TZ as _VIENNA_TZ, plausible_moment

# A date written in a title or text follows the rule of PLAUSIBLE_YEARS too.

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
# mandatory, matching the legacy pattern). ``am`` counts too: a title like
# ``Veranstaltung am 04.10.2026`` names the one day the notice is about,
# while WL's ``time.start`` is the publication day (2026-09-30), and the
# feed read ``[30.09.2026 – 04.10.2026]`` for a two-hour event.
_DATE_NUMERIC_RE = re.compile(
    # The 4-digit alternative is tried BEFORE the 2-digit one (ordered
    # alternation) so "2026" captures all four digits. A 2-digit year like
    # "26" is now accepted and expanded to 2026 in _extract_day_month_year,
    # instead of being captured as None and routed through the
    # nearest-occurrence year heuristic (which can resolve to the wrong year
    # when the title's "ab" date lies in the past relative to the API start).
    r"\ba[bm]\s+(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})?",
    re.IGNORECASE,
)
# ``ab DD. <Monat> [YYYY]`` — spelled-out form WL uses for advance
# notices (e.g. ``ab 07. April 2026``). The legacy code ignored this
# shape entirely, so those start dates silently fell back to the API
# publication date.
_DATE_MONTHNAME_RE = re.compile(
    r"\ba[bm]\s+(\d{1,2})\.?\s*("
    + "|".join(re.escape(m) for m in _MONTHS_DE)
    + r")\b(?:\s+(\d{4}))?",
    re.IGNORECASE | re.UNICODE,
)


def _expand_two_digit_year(year_str: str | None) -> int | None:
    """Expand an optional captured year.

    A 2-digit year (``"26"``) becomes ``2026``; a 4-digit year passes
    through unchanged; ``None`` (no year captured) stays ``None``.
    """
    if not year_str:
        return None
    year = int(year_str)
    if len(year_str) == 2:
        year += 2000
    return year


def _extract_day_month_year(title: str) -> tuple[int, int, int | None] | None:
    """Return ``(day, month, year_or_None)`` from the first ``ab`` date.

    Tries the numeric form first, then the spelled-out month form. The
    two patterns are mutually exclusive, so order only decides which
    wins when a title (pathologically) carries both.
    """
    match = _DATE_NUMERIC_RE.search(title)
    if match:
        day_str, month_str, year_str = match.groups()
        return int(day_str), int(month_str), _expand_two_digit_year(year_str)
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
    """Extract an ``ab``/``am <Datum>`` start date from a Wiener-Linien title.

    Recognises both the numeric ``ab DD.MM.[YYYY]`` and the spelled-out
    ``ab DD. <Monat> [YYYY]`` forms (incl. the Austrian ``Jänner`` /
    ``Feber``). A missing year is resolved against *reference_date*
    (default: now in UTC) via :func:`_resolve_missing_year`. Returns a
    midnight Europe/Vienna datetime, or ``None`` when no parseable date
    is present or its year lies outside ``PLAUSIBLE_YEARS``.
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
        return plausible_moment(datetime(year, month, day, tzinfo=_VIENNA_TZ))
    except ValueError:
        return None


__all__ = [
    "_MONTHS_DE",
    "_expand_two_digit_year",
    "_resolve_missing_year",
    "extract_date_from_title",
]
