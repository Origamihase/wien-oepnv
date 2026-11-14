"""The period a Wiener Linien notice names in its text: start, end and duration under "Zeitraum:", and the stop notice with its "Dauer:"."""

from __future__ import annotations

import calendar
import html
import re
from datetime import date, datetime, timedelta, UTC
from ..utils.vienna_clock import VIENNA_TZ as _VIENNA_TZ, plausible_moment

from .wl_news_kind import lead_text
from .wl_title_dates import _MONTHS_DE, _expand_two_digit_year, _resolve_missing_year

# The "Zeitraum:" section of a WL notice names the real start; ``time.start``
# is the publication day (2026-10-01: 7 of 34 notices began later, "29B/N25:
# Adolf-Loos-Gasse" published 30.09. read "[30.09.2026 – 31.12.2026]" for
# "Ab Montag, 05. Oktober 2026"). The first date after the heading is the
# start ("Ab Samstag, 12. September 2026, Betriebsbeginn …", "Von 08.
# September 2026 bis Ende Oktober 2026"). A network change heads its date
# "Zeitpunkt:" ("Ab 07. September 2026", 9A; 2026-11-07, ``wl_news_kind``).
_PERIOD_HEADING_RE = re.compile(r"Zeit(?:raum|punkt)\s*:", re.IGNORECASE)
# A stop relocation or closure names its period under "Dauer:" instead
# ("Haltestelle: Fultonstraße / Von: … / Nach: … / Dauer: Ab 05. Oktober
# 2026, etwa 07:00 Uhr für etwa zwei Wochen / Grund: Gleisbau"); all 37 such
# notices in the raw data of 2026-10-04 do, none has "Zeitraum:". Without
# it "26E/N20: Fultonstraße", published 28.09., read "[Bis 28.09.2027]" (the
# 11:11 end) a week before it began. Only there: four Störungen use
# "Dauer:" for a clock time or "Bis 30.10.2026 Betriebsschluss", which the
# start rule would take for a begin.
STOP_NOTICE_LEAD_RE = re.compile(r"^\s*Haltestellen(?:verlegung|auflassung)\b", re.IGNORECASE)
_STOP_PERIOD_HEADING_RE = re.compile(r"\bDauer\s*:", re.IGNORECASE)


def is_stop_notice(description: str) -> bool:
    """Whether the WL text *description* is a stop relocation or closure.

    It leads with "Haltestellenverlegung …" or "Haltestellenauflassung …"
    (:data:`STOP_NOTICE_LEAD_RE`); its title names the stop, not a subject
    ("68A: Laaer Berg, Kurpark Nordeingang"), so the facility drop
    (``_facility_drop_reason``) and the fuzzy merge (``_may_merge``) must
    not read that title as one.
    """
    return STOP_NOTICE_LEAD_RE.match(lead_text(description)) is not None


_PERIOD_DATE_RE = re.compile(
    r"\b(\d{1,2})\.\s*(?:(\d{1,2})\.(\d{4}|\d{2})?|("
    + "|".join(re.escape(m) for m in _MONTHS_DE)
    + r")\b(?:\s+(\d{4}))?)",
    re.IGNORECASE | re.UNICODE,
)
# How far behind the heading the start may stand; beyond it the text has
# moved on to the measures and their own dates.
_PERIOD_WINDOW = 120


def _period_text(description: str) -> str | None:
    """The plain text behind the "Zeitraum:" heading (a stop notice's "Dauer:"), ``None`` without one."""
    if not description:
        return None
    text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", description[:20000])).split())
    heading = _PERIOD_HEADING_RE.search(text)
    if heading is None and STOP_NOTICE_LEAD_RE.match(text):
        heading = _STOP_PERIOD_HEADING_RE.search(text)
    return text[heading.end() :] if heading else None


def extract_start_from_description(
    description: str, reference_date: datetime | None = None
) -> datetime | None:
    """The start date in the "Zeitraum:" section of a WL notice, or ``None``.

    Midnight Europe/Vienna, like :func:`extract_date_from_title`; a missing
    year resolves the same way. ``None`` without the heading, without a
    date right behind it, or for a year outside ``PLAUSIBLE_YEARS``.
    """
    period = _period_text(description)
    match = _PERIOD_DATE_RE.search(period[:_PERIOD_WINDOW]) if period else None
    if match is None:
        return None
    day_str, month_num, year_num, month_word, year_word = match.groups()
    day = int(day_str)
    month = int(month_num) if month_num else _MONTHS_DE[month_word.lower()]
    year = _expand_two_digit_year(year_num) if month_num else (int(year_word) if year_word else None)
    if year is None:
        year = _resolve_missing_year(month, day, reference_date or datetime.now(UTC))
        if year is None:
            return None
    try:
        return plausible_moment(datetime(year, month, day, tzinfo=_VIENNA_TZ))
    except ValueError:
        return None


# The end in the same section: "bis voraussichtlich Ende Oktober 2026",
# "bis Ende 2026", "bis Sonntag, 6. September 2026", "bis 31.10.2026",
# "bis Oktober 2027". Only up to "Maßnahme", where the section ends: "bis
# 05:00 Uhr" of a nightly window or a detour "bis Schottenring" must not
# pass for an end date. "auf Dauer von etwa sechs Wochen" or "bis etwa
# Mitte November" name no day and give ``None``.
_MONTHS_ALT = "|".join(re.escape(m) for m in _MONTHS_DE)
_PERIOD_END_RE = re.compile(
    r"\bbis\s+(?:(?:voraussichtlich|etwa|ca\.|circa)\s+)*"
    r"(?:(?:Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag),?\s+)?"
    r"(?:Ende\s+(?:(?P<end_month>" + _MONTHS_ALT + r")\b(?:\s+(?P<end_month_year>\d{4}))?"
    r"|(?P<end_year>\d{4}))"
    r"|(?P<day>\d{1,2})\.\s*(?:(?P<month_num>\d{1,2})\.(?P<year_num>\d{4}|\d{2})?"
    r"|(?P<month_word>" + _MONTHS_ALT + r")\b(?:\s+(?P<year_word>\d{4}))?)"
    r"|(?P<bare_month>" + _MONTHS_ALT + r")\s+(?P<bare_year>\d{4}))",
    re.IGNORECASE | re.UNICODE,
)
_PERIOD_SECTION_END_RE = re.compile(r"Maßnahme|Ersatz:", re.IGNORECASE)
_PHASE_RE = re.compile(r"\bPhase\b", re.IGNORECASE)
_PERIOD_END_WINDOW = 200


def _last_day(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _first_year_from(month: int, day: int | None, begin: date) -> int | None:
    """The first year in which ``day.month.`` (or the month's last day) is not before *begin*.

    ``None`` when the day exists in none of the next eight years (only a
    29.02. could be missing, and every fourth year has one).
    """
    for year in range(begin.year, begin.year + 9):
        try:
            last = date(year, month, day if day is not None else _last_day(year, month))
        except ValueError:
            continue
        if last >= begin:
            return year
    return None


def _end_day(
    g: dict[str, str | None], reference: datetime, begin: date | None = None
) -> tuple[int, int, int] | None:
    """``(year, month, day)`` of the last day an ``_PERIOD_END_RE`` match names.

    WL often writes the year only at the start: "Ab 24. August 2026, etwa
    06:00 Uhr, bis 07. September", "Ab Montag, 28. Dezember 2026 bis
    Mittwoch, 06. Jänner". An end without a year is then the first such
    day on or after that start (*begin*), across the turn of the year too.
    Without a start in the text it is the occurrence nearest *reference*.
    """
    if g["end_year"]:
        return int(g["end_year"]), 12, 31
    month_name = g["bare_month"] or g["end_month"]
    if month_name:
        month = _MONTHS_DE[month_name.lower()]
        year_str = g["bare_year"] or g["end_month_year"]
        if year_str:
            year: int | None = int(year_str)
        elif begin is not None:
            year = _first_year_from(month, None, begin)
        else:
            year = _resolve_missing_year(month, 1, reference)
        return None if year is None else (year, month, _last_day(year, month))
    day = int(g["day"] or 0)
    if g["month_num"]:
        month = int(g["month_num"])
        year = _expand_two_digit_year(g["year_num"])
    else:
        month = _MONTHS_DE[(g["month_word"] or "").lower()]
        year = int(g["year_word"]) if g["year_word"] else None
    if year is None:
        if begin is not None:
            year = _first_year_from(month, day, begin)
        else:
            year = _resolve_missing_year(month, day, reference)
    return None if year is None else (year, month, day)


def _period_section(description: str) -> str | None:
    """The "Zeitraum:" section up to its measures, ``None`` without one or with phases.

    "Phase 1: … bis 6. September 2026. Phase 2: …" – the first end is not
    the end, and a duration would only be the first phase's.
    """
    period = _period_text(description)
    if not period:
        return None
    section = period[:_PERIOD_END_WINDOW]
    cut = _PERIOD_SECTION_END_RE.search(section)
    section = section[: cut.start()] if cut else section
    return None if _PHASE_RE.search(section) else section


def extract_end_from_description(
    description: str, reference_date: datetime | None = None
) -> datetime | None:
    """The end date in the "Zeitraum:" section of a WL notice, or ``None``.

    23:59 Europe/Vienna of the named day, or of the month's or year's last
    day for "Ende <Monat>" / "Ende <Jahr>" / "<Monat> <Jahr>"; WL's own
    exact ends read 23:59 too. A missing year makes it the first such day
    on or after the section's start (see :func:`_end_day`); without a start
    it resolves like the start. A year outside ``PLAUSIBLE_YEARS``: ``None``.
    """
    section = _period_section(description)
    if section is None:
        return None
    match = _PERIOD_END_RE.search(section)
    if match is None:
        return None
    reference = reference_date or datetime.now(UTC)
    begin = extract_start_from_description(description, reference_date=reference)
    named = _end_day(match.groupdict(), reference, begin.date() if begin else None)
    if named is None:
        return None
    year, month, day = named
    try:
        return plausible_moment(datetime(year, month, day, 23, 59, tzinfo=_VIENNA_TZ))
    except ValueError:
        return None



# A duration instead of an end: "Ab Montag, 05. Oktober 2026, etwa 06:30 Uhr
# auf Dauer von etwa sechs Wochen" (29B/N25), "Von Montag, 05. Oktober 2026,
# auf Dauer von etwa vier Wochen, täglich von 20:00 Uhr bis 05:00 Uhr" (63A).
# Four notices on 2026-10-02, all with an 11:11 end.
# A range ("für etwa zwei bis drei Wochen", "2-3 Monate") is not read on
# purpose (operator decision 2026-10-15, "So lassen"): it counts as no
# duration, like a vague end, so the notice shows no end and stays while WL
# lists it. Nothing wrong shows, and none of the 9,741 distinct texts of all
# providers since 2026-07-01 had one.
_NUMBER_WORDS: dict[str, float] = {
    "eineinhalb": 1.5, "anderthalb": 1.5,
    "einem": 1, "einen": 1, "einer": 1, "eine": 1, "ein": 1, "zwei": 2, "drei": 3, "vier": 4,
    "fünf": 5, "sechs": 6, "sieben": 7, "acht": 8, "neun": 9, "zehn": 10, "elf": 11, "zwölf": 12,
}
_UNIT_DAYS = {"tag": 1, "woche": 7, "monat": 30, "jahr": 365}
_PERIOD_DURATION_RE = re.compile(
    r"\b(?:Dauer\s+von|für)\s+(?:(?:etwa|ca\.|circa|rund|ungefähr|voraussichtlich)\s+)?"
    r"(?P<count>\d{1,2}(?:,\d)?|" + "|".join(_NUMBER_WORDS) + r")\s+"
    r"(?P<unit>Tag(?:e|en)?|Woche(?:n)?|Monat(?:e|en)?|Jahr(?:e|en)?)\b",
    re.IGNORECASE | re.UNICODE,
)


def extract_duration_from_description(description: str) -> timedelta | None:
    """The duration the "Zeitraum:" section names ("auf Dauer von etwa sechs Wochen"), or ``None``.

    Days, weeks, months (30 days) and years (365 days), as a number
    ("1,5" too) or a word up to "zwölf" ("eineinhalb" too). Years came in
    with the decision of 2026-10-08: "1A: Habsburgergasse" ("für etwa ein
    Jahr"), "57A: Haus des Meeres" ("für ca. 1 Jahr") and "26A, N20:
    Siebeckstraße" ("für etwa 1,5 Jahre") kept WL's 11:11 expiry two and
    three years out. ``None`` without the heading, with phases or without a
    duration before the measures.
    """
    section = _period_section(description)
    match = _PERIOD_DURATION_RE.search(section) if section else None
    if match is None:
        return None
    count_str = match.group("count").lower()
    count = _NUMBER_WORDS[count_str] if count_str in _NUMBER_WORDS else float(count_str.replace(",", "."))
    unit = match.group("unit").lower()
    days = next(per_unit for name, per_unit in _UNIT_DAYS.items() if unit.startswith(name))
    return timedelta(days=count * days) if count > 0 else None


__all__ = [
    "STOP_NOTICE_LEAD_RE",
    "extract_duration_from_description",
    "extract_end_from_description",
    "extract_start_from_description",
    "is_stop_notice",
]
