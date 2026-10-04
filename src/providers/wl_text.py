"""Utility functions for Wiener Linien text handling and filtering."""

from __future__ import annotations

import calendar
import html
import re
from datetime import date, datetime, timedelta, UTC
from zoneinfo import ZoneInfo

_VIENNA_TZ = ZoneInfo("Europe/Vienna")

# ---------------- Relevanz-/Ausschluss-Filter ----------------

KW_RESTRICTION = re.compile(
    # Match the restriction root anywhere inside a German compound noun
    # (``Baustelle``, ``Teilsperre``, ``Streckensperre``, ``Bauarbeiten``,
    # ``Verspätungen``, ``Einschränkungen``, ``Signalstörung`` …). The
    # bare-root pattern with ``\b`` on BOTH sides MISSED every inflection /
    # compound of the prefix-roots below (``sperr``, ``baustell``, ``verspät``,
    # ``einschränk``, ``unterbrech`` …): only the handful of roots that happen
    # to be standalone words (``umleitung``, ``verkehr``, ``gesperrt``,
    # standalone ``Störung`` / ``Ausfall``) ever matched, so the bulk of real
    # WL disruption items were silently dropped at the mandatory inclusion
    # gate in ``wl_fetch`` (and not rescued past ``KW_EXCLUDE``). Same fix
    # shape as the sibling ``FACILITY_ONLY`` regex below, which documents this
    # exact ``\b``-on-both-sides pitfall. Keyword list is unchanged.
    r"""
    \b\w*(
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
    )\w*\b
    # The same measures as a verb. A WL news item states its measure in a
    # sentence, and the roots above are nouns: "18: LCC-Herbstmarathon am
    # 11.10.2026" ("wird die Linie 18 kurz geführt") and "U6: Neue Donau,
    # kein Halt Richtung Floridsdorf" ("wird ... durchfahren", only notice
    # with a line for that closure) were both dropped at the news gate
    # (raw data ``data/raw/wl/verworfen.json``, 2026-10-04), while
    # "47B: Laufveranstaltung" passed on an incidental "Umleitung".
    | \bkurz\s*ge(?:führt|fuehrt)\b               # short service
    | \bumgeleitet\b                              # detoured
    | \bdurchf(?:ahr|ähr|aehr)\w*                 # station passed without stop
    | \bkein(?:en)?\s+(?:halt|betrieb)\b          # no stop / no service
    | \beingestellt\b                             # service suspended
    | \bentf(?:ällt|aellt|allen)\b                # stop/trip cancelled
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


# ``ab DD.MM.[YY|YYYY]`` — the advance-notice date WL glues onto titles
# (``Bauarbeiten ab 14.09.26``). The whole phrase is dropped from the
# rendered title because the validity window is carried by the item's
# ``starts_at`` / ``ends_at`` fields (and rendered as the ``[…]`` time
# line), so repeating it in the title is redundant.
#
# The year alternation MUST cover the 2-digit form. Pre-fix the pattern
# read ``(?:\d{4})?`` — an optional FOUR-digit year — so a real title
# ``"Bauarbeiten ab 14.09.26"`` matched only through the month's
# trailing dot: ``" ab 14.09."`` was removed and the orphaned ``"26"``
# stayed behind. The trailing ``\s+`` collapse then glued it onto the
# preceding word and the feed published ``"17A: Bauarbeiten26"``
# (observed live in ``docs/feed.xml`` and ``cache/wl_9d709a/events.json``
# on 2026-09-09; see ``docs/archive/audits/audit-title-bauarbeiten26-2026-09-09.md``).
#
# Ordered alternation (``\d{4}`` before ``\d{2}``) makes "2026" consume
# all four digits; the trailing ``(?!\d)`` guard prevents a partial
# consume on a malformed 3- or 5-digit year — the pattern then fails
# as a whole and the title is left untouched rather than half-stripped
# (the exact failure mode this fix exists to prevent).
_AB_DATE_RE = re.compile(
    r"(?:^|\s+)ab\s+\d{1,2}\.\d{1,2}\.(?:\d{4}|\d{2})?(?!\d)",
    re.IGNORECASE,
)


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
    # Order matters: drop the ``ab <Datum>`` phrase BEFORE the generic
    # label head. With the reverse order a title carrying a 4-digit year
    # (``"Bauarbeiten ab 14.09.2026"``) lost its label first — the
    # remainder ``"ab 14.09.2026"`` passes ``_is_informative`` because
    # ``2026`` is a 4-character alphanumeric run — and the date phrase
    # then no longer matched, because it had become the start of the
    # string with no leading whitespace. The published title degenerated
    # to the contentless ``"17A: ab 14.09.2026"``. Stripping the date
    # first leaves ``"Bauarbeiten"``, which the label head then keeps
    # (an empty remainder is not informative).
    t = _AB_DATE_RE.sub("", t)
    stripped = _LABEL_HEAD_RE.sub("", t)
    if stripped and _is_informative(stripped):
        t = stripped
    t = re.sub(r"[<>«»‹›]+", "", t)  # spitze Klammern/Anführungen
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


# The "Zeitraum:" section of a WL notice names the real start; ``time.start``
# is the publication day. On 2026-10-01, 7 of 34 notices began later than
# their ``starts_at``, among them "29B/N25: Adolf-Loos-Gasse" (published
# 30.09., "Ab Montag, 05. Oktober 2026") in the feed as
# "[30.09.2026 – 31.12.2026]". The first date after the heading is the
# start: "Ab Samstag, 12. September 2026, Betriebsbeginn (Nacht von 11. auf
# 12. September) …", "Von 08. September 2026 bis Ende Oktober 2026",
# "Montag, 3. August 2026, bis Ende September 2026".
_PERIOD_HEADING_RE = re.compile(r"Zeitraum\s*:", re.IGNORECASE)
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
    """The plain text behind the "Zeitraum:" heading, ``None`` without one."""
    if not description:
        return None
    text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", description[:20000])).split())
    heading = _PERIOD_HEADING_RE.search(text)
    return text[heading.end() :] if heading else None


def extract_start_from_description(
    description: str, reference_date: datetime | None = None
) -> datetime | None:
    """The start date in the "Zeitraum:" section of a WL notice, or ``None``.

    Midnight Europe/Vienna, like :func:`extract_date_from_title`; a missing
    year resolves the same way. ``None`` without the heading or without a
    date right behind it.
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
        return datetime(year, month, day, tzinfo=_VIENNA_TZ)
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
    it resolves against *reference_date* like the start.
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
        return datetime(year, month, day, 23, 59, tzinfo=_VIENNA_TZ)
    except ValueError:
        return None



# A duration instead of an end: "Ab Montag, 05. Oktober 2026, etwa 06:30 Uhr
# auf Dauer von etwa sechs Wochen" (29B/N25), "Von Montag, 05. Oktober 2026,
# auf Dauer von etwa vier Wochen, täglich von 20:00 Uhr bis 05:00 Uhr" (63A).
# Four notices on 2026-10-02, all with an 11:11 end.
_NUMBER_WORDS = {
    "einen": 1, "einer": 1, "eine": 1, "ein": 1, "zwei": 2, "drei": 3, "vier": 4,
    "fünf": 5, "sechs": 6, "sieben": 7, "acht": 8, "neun": 9, "zehn": 10, "elf": 11, "zwölf": 12,
}
_UNIT_DAYS = {"tag": 1, "woche": 7, "monat": 30}
_PERIOD_DURATION_RE = re.compile(
    r"\b(?:Dauer\s+von|für)\s+(?:(?:etwa|ca\.|circa|rund|ungefähr|voraussichtlich)\s+)?"
    r"(?P<count>\d{1,2}|" + "|".join(_NUMBER_WORDS) + r")\s+"
    r"(?P<unit>Tag(?:e|en)?|Woche(?:n)?|Monat(?:e|en)?)\b",
    re.IGNORECASE | re.UNICODE,
)


def extract_duration_from_description(description: str) -> timedelta | None:
    """The duration the "Zeitraum:" section names ("auf Dauer von etwa sechs Wochen"), or ``None``.

    Days, weeks and months (30 days), as a number or a word up to
    "zwölf". ``None`` without the heading, with phases or without a
    duration before the measures.
    """
    section = _period_section(description)
    match = _PERIOD_DURATION_RE.search(section) if section else None
    if match is None:
        return None
    count_str = match.group("count").lower()
    count = int(count_str) if count_str.isdigit() else _NUMBER_WORDS[count_str]
    unit = match.group("unit").lower()
    days = next(per_unit for name, per_unit in _UNIT_DAYS.items() if unit.startswith(name))
    return timedelta(days=count * days) if count > 0 else None

# ---------------- „Kernbegriff/Topic“ für Dedupe ----------------

# Ursachen-Wörter, an denen zwei Upstream-Meldungen als DASSELBE Ereignis
# erkannt werden. Fehlt das Wort hier, fällt :func:`_topic_key_from_title` auf
# den ganzen Titel-Kern zurück — dann ist jede Formulierungsvariante ein
# eigenes Topic, ein eigener Bucket und am Ende ein eigenes Feed-Item.
#
# ``demonstration`` und ``veranstaltung`` fehlten und standen deshalb am
# 2026-09-12 doppelt im Feed:
#
#     38A: Demonstration
#     38A: Demonstration Haltestelle Kahlenberg wird nicht eingehalten
#
# Beide beschreiben dieselbe Sperre der Haltestelle Kahlenberg. Vor PR #1791
# fiel das nicht auf, weil ``_dedupe_items`` sie über den groben
# ``_identity`` (Linie + Tag) blind zusammenwarf und eines davon verwarf.
# Dieses Verwerfen war keine Lösung, sondern eine Maskierung: Es traf
# genauso Meldungen, die wirklich verschieden waren (``49A/50B: Mondweg``
# gegen ``49A/50B: Hüttergasse`` — zwei Straßen).
#
# Der richtige Ort zum Zusammenführen ist dieser hier: Im Bucketing von
# ``fetch_events`` gewinnt der bessere Titel UND die bessere Beschreibung
# (``_title_quality_key`` / ``_description_info_score``), Haltestellen und
# Extras werden vereinigt. Aus den beiden 38A-Meldungen wird dadurch ein
# Item, das beide schlägt: der informative Titel mit dem ausführlichen Text.
#
# ``feuerwehreinsatz`` fehlte aus derselben Reihe (``polizeieinsatz`` und
# ``rettungseinsatz`` standen längst hier) und kostete am 2026-09-12 zwei der
# zehn Plätze im deutschen Feed für EINEN Einsatz:
#
#     64A: Fahrtbehinderung wegen Feuerwehreinsatz
#     64A: Feuerwehreinsatz Betrieb ab Gregorygasse
#
# Neue Tokens deshalb nur mit Beleg aus den Live-Daten aufnehmen — jedes
# zusätzliche Wort führt Meldungen aggressiver zusammen. Der deutsche Feed
# (``docs/feed.xml``) hat dabei Vorrang: Er läuft auf Info-Displays mit fest
# begrenzter Item-Zahl, dort ist jeder doppelte Eintrag ein verlorener Platz
# (s. AGENTS.md, „Priorität der Ausgaben").
TITLE_TOPIC_TOKENS = {
    "falschparker",
    "polizeieinsatz",
    "rettungseinsatz",
    "feuerwehreinsatz",
    "demonstration",
    "veranstaltung",
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

# Pre-fix ``betrieb\s+ab.*`` (and the sibling ``betrieb\s+nur.*``) used a
# greedy ``.*`` that swallowed every downstream token through to end-of-
# string, eating legitimate topic tokens — ``Sperre Betrieb ab Karlsplatz
# mit Unfall`` collapsed to ``sperre`` (the ``unfall`` topic token was
# lost), so two unrelated incidents that happened to follow a "Betrieb
# ab ..." preamble degenerated to the same ``topic_key`` and collided in
# the WL identity / dedup pipeline. Replacing ``.*`` with
# ``\s+\S+`` strips the canonical "Betrieb ab <Ort>" / "Betrieb nur
# <Ort>" phrasing (one location word, matching what the upstream feed
# actually emits — verified against current cache snapshots) and stops
# at the next whitespace so downstream tokens survive for the topic-key
# extractor at ``_topic_key_from_title``.
_GENERIC_FILLER = re.compile(
    r"\b(fahrtbehinderung|verkehrsbehinderung|behinderung|störung|stoerung|hinweis|meldung|serviceinfo|service\-info|"
    r"betrieb\s+ab\s+\S+|betrieb\s+nur\s+\S+)\b",
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
    "extract_duration_from_description",
    "extract_end_from_description",
    "extract_start_from_description",
]
