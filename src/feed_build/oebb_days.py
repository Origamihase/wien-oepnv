"""Which Vienna days an ÖBB measure closes the line, read from its own text.

Feed order, operator decision 2026-11-08: a running ÖBB closure ranks like a
notice with higher priority, behind the current incidents and ahead of every
other notice, but only on a day it really closes the line
(:func:`_is_oebb_closure_day`, used by ``item_meta._recency_sort_key``).

Michael's reason (08.11.2026): "Auf ungeplante Störungen kann man sich
nicht vorbereiten. Auf geplante Bauarbeiten, Sperren etc schon." So the
unplanned incidents keep the front (tier 0); a planned closure stays a
notice, but on its closure days it stands ahead of the other notices.

Why: "Wien Hauptbahnhof ↔ Gramatneusiedl" (31.10. to 30.11.2026, no local
trains every Saturday and Sunday night) entered the feed on 31.10. and
fell to places 11 to 14 behind newer WL stop relocations on 07.11.; it was
not on the displays in its own closure nights. Michael on 08.11.2026:
"Störungen gehen immer vor." and "Man könnte es auch als Hinweis mit
höherer Priorität formulieren". The category alone cannot carry it: WL
sends planned works tickers ("26E: Gleisbauarbeiten", "S80: ÖBB-Ersatzbus")
as "Störung", and those are notices by his decision of 03.11.2026.

The days come from the lead sentence, the sentence the feed shows first
(``_oebb_shown_sentences`` in :mod:`.oebb_lead`): ÖBB adds further sections
for single trains ("HALTAUSFALL: Von 11.09.2026 bis 09.10.2026 … kann der
Zug RJ 263 in Wien Hütteldorf … nicht halten."), and their days would put a
text about another week in front ("Hbf ↔ Westbahnhof … von 23.10. bis 27.10.
keine Fernverkehrszüge" on 29.10.). When the display leads with a later
section because it names the title's route, that section gives the days:
under "Wien Hütteldorf ↔ Wien Hauptbahnhof" (17.10.2026) the feed showed
"Teilausfall: Von 18.10.2026 bis 19.10.2026 kann der Zug RJ 263 …", while
the days came from ÖBB's first sentence about changed times between Salzburg
and Flughafen Wien (independent check 2026-11-11). A lead section without a
date of its own takes the days of ÖBB's first sentence, where ÖBB writes
them ("REX 3/REX 1: Wien Hauptbahnhof ↔ Deutsch Wagram", cache 04.09.2026:
"REX 1-Züge werden … über Wien Stadlau Bahnhst umgeleitet.").

A measure ranks as a closure only when its lead sentence says that no
trains run or stop (:data:`_CLOSES_LINE_RE`: "keine Nahverkehrszüge fahren",
"keine R40- und S40-Züge fahren", "keine Züge halten", "keine Fahrten
möglich", "Zugfahrten derzeit nicht möglich"). A single cancelled train, a
list of trains, changed times or a diversion closes nothing: "der S1-Zug
23626" (between Deutsch Wagram and Gänserndorf on 14., 15., 21., 22. and
28.11.2026, outside Vienna) would have stood ahead of every WL notice on
those days; from 01.10. to 11.11. single trains (S1 23626, S2 23360, REX64
2679, RJ 263) pushed WL notices out of the first ten in 152 of 1,220
replayed feed states (independent check 2026-11-11, operator decision of
the same day). Since the same day a notice whose lead names only single
trains does not enter the feed at all (:mod:`.oebb_single_trains`,
"Einzelzüge sollen nicht in den Feed.").

The lead sentence gives its days by the dates ÖBB writes ("von 03.11.2026
bis 10.11.2026", "zwischen 29.10.2026 und 01.11.2026", "am 25.10.2026 und
01.11.2026", "am 03.02.2027, 03.03.2027 … und 02.06.2027", "von 23.10.2026
(23:00 Uhr) bis 27.10.2026 (04:00 Uhr)"); a day without its year takes the
year of the next full date ("von 08.03. bis 10.03.2027"), two digits stand
for 20xx ("am 03.02.27"). A weekday window narrows them ("(jeweils von
Samstag 23:15 Uhr bis Sonntag 04:40 Uhr und von Sonntag 23:15 Uhr bis Montag
04:40 Uhr)" gives Saturday and Sunday, the evenings the closure begins;
"(jeweils Sonntags … und Montags …)" Sunday and Monday). Closure hours
within a day end the closure there unless the next day is closed too:
"R95: Wien Hauptbahnhof ↔ Felixdorf" ("(jeweils 14:00 Uhr - 22:00 Uhr)")
stood ahead of the WL notices until 23:30 on 10.11.2026, after its closure
had ended. A date behind "bis" is an end, no day of the measure ("bis
voraussichtlich am 07.10.2026").

A text that names no day at all counts on every day of the item's own
period: an unplanned disruption ("derzeit", "bis voraussichtlich 09:00
Uhr"). A text that names days in a form the reader does not take ("in der
Nacht von 14.02. auf 15.02.2026", "von 20.10.2026 auf 21.10.2026") is a
planned measure and counts on every day only when it closes the line
(:func:`_names_measure_day`, independent check 2026-11-11).

A closure that runs day after day for more than four weeks leads the
notices only in its first week (operator decision 2026-11-12, card "Soll die
Dauersperre Praterstern–Hauptbahnhof bis Oktober 2027 täglich vorn stehen?":
"Vorrang nur in der ersten Woche"; :func:`_past_first_week`). "Wien
Praterstern ↔ Wien Hauptbahnhof / Wien St. Marx" (07.09.2026 to 31.10.2027,
"von 07.09.2026 bis Ende Oktober 2027 keine Züge fahren") held one of the
ten places in 267 of the 283 published feed states from 07.11., 13:31, to
12.11.2026, 23:00 Vienna time, and would have kept the front every day for
a year, against his sentence of the same day: "Alte (aber noch gültige)
Hinweise an den letzten Plätzen." After the first week such a closure ranks
like the other notices and counts there from its first day
(:func:`_long_closure_start`).

What counts is the unbroken run of closure days around today
(:func:`_closure_run`):

* Weekend nights (Gramatneusiedl) are runs of two days and keep the front
  in every closure night; a closure of up to four weeks keeps it
  throughout; a day without closure between two runs starts a new run.
* A closure in every night ("von 16.01.2027 bis 08.03.2027 (jeweils 00:30
  Uhr - 04:00 Uhr)") has no day between its nights: one run, long after
  four weeks.
* A text that names no day, or a first day without a last one the reader
  takes ("bis Ende Oktober 2027"), gives the item's own period as its run.
  That holds for every undated ÖBB text that counts on each day, a closure
  or not. A run without a known end counts as long as it has lasted (ÖBB
  dates every item, so the feed has not seen one yet).
* A text that names several days in a form the reader does not take ("in
  den Nächten von 13.02. auf 14.02. und von 20.02. auf 21.02.2027") is
  left as it was: every day of its period, however long.
* A range of more than 400 days is not read (:func:`_oebb_closure_days`);
  such a text falls under the period rule above, weekday window or not,
  written "von … bis …" or "zwischen … und …".

A current incident keeps the front and its moment whatever its period
("Störungen gehen immer vor.", ``item_meta._recency_sort_key``), and a long
closure past its first week hands the one slot of its ÖBB title to a
closure of the same route on its closure day
(``filters._defer_repeated_route_titles``).

The length is counted in calendar days with closure: Monday 22:00 to Monday
04:30 four weeks later closes on 29 days and is long.
"""

from __future__ import annotations

import html
import re
from datetime import date, datetime, time, timedelta

from ..feed_types import FeedItem
from .base import _OEBB_SOURCE, _SENTENCE_SPLIT_RE, _VIENNA_TZ, _parse_datetime, _to_utc
from .oebb_lead import _oebb_shown_sentences

__all__ = ["_oebb_closure_days", "_oebb_closes_line", "_is_oebb_closure_day", "_long_closure_start"]

# A closure that runs day after day for more than ``_LONG_CLOSURE_DAYS``
# leads the notices only on its first ``_LONG_CLOSURE_FRONT_DAYS`` days
# (operator decision 2026-11-12, :func:`_past_first_week`).
_LONG_CLOSURE_DAYS = 28
_LONG_CLOSURE_FRONT_DAYS = 7

_DATE = r"(\d{1,2})\.(\d{2})\.(\d{4})"
_TIME = r"(?:\s*\(\d{1,2}:\d{2} Uhr\))?"
# "Von" and "Am" open a section: "TEILAUSFALL: Von 18.10.2026 bis 19.10.2026 …".
# A comma or "und" continues a list: "am 03.02.2027, 03.03.2027, … und
# 02.06.2027", "von 07.02. bis 08.02., 12.02. bis 15.02., 19.02. bis
# 06.03.2027".
_RANGE_RE = re.compile(rf"(?:\b[Vv]on|,|\bund) {_DATE}{_TIME} bis {_DATE}(?:\s*\((\d{{1,2}}):(\d{{2}}) Uhr\))?")
_SINGLE_RE = re.compile(rf"(?:\b[Aa]m|\bund|,) {_DATE}")
# "zwischen 29.10.2026 und 01.11.2026" (Golling, ÖBB RSS 10.2026) is a range.
_BETWEEN_RE = re.compile(rf"\bzwischen {_DATE}{_TIME} und {_DATE}(?:\s*\((\d{{1,2}}):(\d{{2}}) Uhr\))?")
# Closure hours within one day end the closure there on a day the next day
# does not continue: "(jeweils 14:00 Uhr - 22:00 Uhr)" (R95 Wien Hbf –
# Felixdorf on 13.10. and 10.11.2026), "(von 01:10 Uhr bis 04:10 Uhr)" (S45,
# 01.11.2026). "von 01.11.2026 bis 16.11.2026 (jeweils 00:10 Uhr - 04:30
# Uhr)" keeps every day but the last whole: the next night's closure is
# coming. Hours across midnight ("jeweils von 23:15 Uhr bis 04:40 Uhr") keep
# the whole day (:func:`_hours_end`).
_HOURS_RE = re.compile(r"\b(\d{1,2}):(\d{2})\s*Uhr\s*(?:-|–|bis)\s*(\d{1,2}):(\d{2})\s*Uhr")
# A day ÖBB writes without its year or with two digits for it: "von 08.03.
# bis 10.03.2027 und von 15.03. bis 17.03.2027" (Spittal–Lienz), "am 02.07.,
# 06.08., 03.09., 01.10. und 05.11.2026", "von 07.02 bis 08.02.", "am
# 03.02.27, 03.03.27, …" (Pöchlarn–Scheibbs). It takes the year of the next
# full date (:func:`_with_years`).
_SHORT_DATE_RE = re.compile(r"\b(\d{1,2})\.(\d{2})(?:\.(\d{4}|\d{2})?)?(?!\d)(?!\.\d)")
# A date behind "bis" is an end: "bis voraussichtlich am 07.10.2026" is the
# expected end of a disruption, no day of a measure. "Zugfahrten bis
# voraussichtlich am 07.10.2026, 23:59 Uhr nur eingeschränkt möglich"
# (Gramatneusiedl–Bruck/Leitha, 04.10.2026) reads like its twin without "am"
# (independent check 2026-11-11).
_UNTIL_BEFORE_RE = re.compile(r"\bbis(?:\s+\w+){0,2}\s*$")
# "zwischen 06.10.2025 und 16.11.2026": the second date ends the range like
# one behind "bis" (independent check 2026-11-13). Only this bare form, with
# or without clock times in brackets; a list of days ("am 13.02. und
# 20.02.") keeps both.
_BETWEEN_END_RE = re.compile(
    r"(\bzwischen \d{1,2}\.\d{2}(?:\.(?:\d{4}|\d{2})?)?(?:\s*\(\d{1,2}:\d{2} Uhr\))?) und(?= \d{1,2}\.\d{2}(?!\d))"
)
# The lead says that no trains run or stop on its days: "keine
# Nahverkehrszüge fahren", "keine R40- und S40-Züge fahren", "keine Züge
# halten", "keine Fahrten möglich", "kein Zugverkehr möglich". The verb may
# stand behind dates or at the end of a list the trains share, anywhere in
# the same sentence: "Zwischen Wien Westbahnhof (U) und Wien Hütteldorf
# Bahnhof (U) können … von 03.11.2026 bis 10.11.2026 keine S50-Züge von
# 02.11.2026 bis 10.11.2026 die S50-Züge 20063, … nicht fahren." (ÖBB,
# 04.10.2026). Over the 1,693 distinct sentences ÖBB sent from 24.08. to
# 11.11.2026 that list form is the only one the verb's place adds
# (independent check 2026-11-11). "der S1-Zug 23626 … nicht fahren", "die
# REX51-Züge 1604, … nicht fahren", "einige Fernverkehrszüge mit geänderten
# Fahrzeiten" and "der Zug RJ 12265 umgeleitet" close nothing. "Zugfahrten
# derzeit nicht möglich" closes the line too (Wien Hbf – Matzleinsdorfer Platz,
# 18.10.2026), "Zugfahrten … nur eingeschränkt möglich" does not.
_CLOSES_LINE_RE = re.compile(
    r"\bkeine?\s+[^.,;:()]{0,40}?(?:Züge|züge|Fahrten|Zugverkehr)\b"
    r"(?:[^.;]|\.(?=\d))*?\b(?:fahren|verkehren|halten|möglich)\b"
    r"|\b(?:Zugfahrten|Zugverkehr)\b(?:[^.,;]|[.,](?=\s?\d))*?\bnicht\s+möglich\b"
)
# ÖBB breaks digits with markup: "von <b>2</b>4.10.2026", "am 25 .10.2026".
_SPLIT_DAY_RE = re.compile(r"(?<!\d)(\d) (\d)\.(\d{2}\.\d{4})")
_SPACE_DOT_RE = re.compile(r"(\d) \.(\d{2}\.\d{4})")
_WINDOW_RE = re.compile(r"\(jeweils ([^()]*)\)")
_WEEKDAYS = {
    "montag": 0, "dienstag": 1, "mittwoch": 2, "donnerstag": 3,
    "freitag": 4, "samstag": 5, "sonntag": 6,
}
# "Sonntags 00:10 Uhr - 04:30 Uhr und Montags …" (Felixdorf – Wr.Neustadt) names days too.
_WINDOW_START_RE = re.compile(r"\bvon (" + "|".join(_WEEKDAYS) + r")s?\b", re.IGNORECASE)
_WINDOW_DAY_RE = re.compile(r"\b(" + "|".join(_WEEKDAYS) + r")s?\b", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def _plain(description: str) -> str:
    text = html.unescape(_TAG_RE.sub(" ", description))
    text = re.sub(r"\s+", " ", text)
    text = _SPLIT_DAY_RE.sub(r"\1\2.\3", text)
    return _SPACE_DOT_RE.sub(r"\1.\2", text)


def _day(match: re.Match[str], first: int) -> date | None:
    try:
        return date(int(match.group(first + 2)), int(match.group(first + 1)), int(match.group(first)))
    except ValueError:
        return None


def _window_weekdays(text: str) -> set[int] | None:
    """Weekdays a "(jeweils …)" window names, or None for every day."""
    days: set[int] = set()
    for window in _WINDOW_RE.findall(text):
        starts = _WINDOW_START_RE.findall(window)
        named = starts or _WINDOW_DAY_RE.findall(window)
        days.update(_WEEKDAYS[name.casefold()] for name in named)
    return days or None


def _hours_end(text: str) -> time | None:
    """The latest end of the closure hours *text* names within one day, or None.

    None as well when one of the hours runs across midnight: such a closure
    keeps its whole days (:data:`_HOURS_RE`).
    """
    ends: list[time] = []
    for match in _HOURS_RE.finditer(text):
        start = (int(match.group(1)), int(match.group(2)))
        end = (int(match.group(3)), int(match.group(4)))
        if end <= start or end[0] > 23 or end[1] > 59:
            return None
        ends.append(time(*end))
    return max(ends) if ends else None


def _with_years(text: str) -> str:
    """*text* with every day written in full (:data:`_SHORT_DATE_RE`).

    A day without its year takes the year of the next full date after it,
    the year before when its month and day come later than that date's
    ("von 28.12. bis 03.01.2027"); two digits stand for 20xx. A day without
    a full date after it stays as it is and is not read.
    """
    pieces: list[str] = []
    end = len(text)
    anchor: date | None = None
    for match in reversed(list(_SHORT_DATE_RE.finditer(text))):
        day, month, year = int(match.group(1)), int(match.group(2)), match.group(3)
        try:
            if year is not None:
                anchor = date(int(year) + (2000 if len(year) == 2 else 0), month, day)
            elif anchor is not None:
                later = (month, day) > (anchor.month, anchor.day)
                anchor = date(anchor.year - (1 if later else 0), month, day)
            else:
                continue
        except ValueError:
            continue
        pieces.append(text[match.end():end])
        pieces.append(f"{anchor.day:02d}.{anchor.month:02d}.{anchor.year}")
        end = match.start()
    pieces.append(text[:end])
    return "".join(reversed(pieces))


def _oebb_closure_days(description: str) -> dict[date, time | None] | None:
    """The days the lead sentence says the line is closed, or None if it names none.

    Each day maps to the clock time the closure ends on it, or None for the
    whole day: "bis 27.10.2026 (04:00 Uhr)" closes 27.10. only until 04:00,
    and a text about a closure that ended at 04:00 must not lead that day.
    """
    text = _with_years(_SENTENCE_SPLIT_RE.split(_plain(description).strip(), maxsplit=1)[0])
    days: dict[date, time | None] = {}

    def add(day: date, until: time | None = None) -> None:
        if day in days and days[day] is None:
            return
        earlier = days.get(day)
        days[day] = None if until is None else max(until, earlier) if earlier else until

    for match in [*_RANGE_RE.finditer(text), *_BETWEEN_RE.finditer(text)]:
        first, last = _day(match, 1), _day(match, 4)
        if first is None or last is None or last < first or (last - first).days > 400:
            continue
        for n in range((last - first).days):
            add(first + timedelta(days=n))
        until = None
        if match.group(7) is not None and int(match.group(7)) < 24:
            until = time(int(match.group(7)), int(match.group(8)))
        add(last, until)
    ranged = _BETWEEN_RE.sub(" ", _RANGE_RE.sub(" ", text))
    for match in _SINGLE_RE.finditer(ranged):
        if _UNTIL_BEFORE_RE.search(ranged[:match.start()]):
            continue
        single = _day(match, 1)
        if single is not None:
            add(single)
    if not days:
        return None
    weekdays = _window_weekdays(text)
    if weekdays is not None:
        days = {day: until for day, until in days.items() if day.weekday() in weekdays}
    hours_end = _hours_end(text)
    if hours_end is not None:
        days = {
            day: hours_end if until is None and day + timedelta(days=1) not in days else until
            for day, until in days.items()
        }
    return days


def _oebb_closes_line(lead: str) -> bool:
    """Whether an ÖBB lead sentence says that no trains run or stop (:data:`_CLOSES_LINE_RE`)."""
    return bool(_CLOSES_LINE_RE.search(_plain(lead)))


def _measure_days_named(sentence: str) -> set[str]:
    """The days an ÖBB sentence names for its measure, as written, readable or not.

    Any date counts but one behind "bis" (:data:`_UNTIL_BEFORE_RE`) or the
    second of "zwischen … und …" (:data:`_BETWEEN_END_RE`): a planned
    measure says when it is ("von 20.10.2026 auf 21.10.2026", "in der Nacht
    von 14.02. auf 15.02.2026"); an unplanned disruption says at most when it
    should end ("bis voraussichtlich 27.08.2026, 23:59 Uhr", "bis
    voraussichtlich 09:00 Uhr"). The strings are counted, not days: a time
    written with a dot ("04.00 Uhr") counts like a day.
    """
    text = _BETWEEN_END_RE.sub(r"\1 bis", _plain(sentence))
    return {match.group(0) for match in _SHORT_DATE_RE.finditer(text) if not _UNTIL_BEFORE_RE.search(text[:match.start()])}


def _names_measure_day(sentence: str) -> bool:
    """Whether an ÖBB sentence names a day of its measure (:func:`_measure_days_named`)."""
    return bool(_measure_days_named(sentence))


def _closure_run(days: dict[date, time | None], today: date) -> tuple[date, date]:
    """First and last day of the unbroken run of closure days around *today*."""
    first = last = today
    one_day = timedelta(days=1)
    while first - one_day in days:
        first -= one_day
    while last + one_day in days:
        last += one_day
    return first, last


def _closure_runs(days: dict[date, time | None]) -> list[tuple[date, date]]:
    """Every unbroken run of closure days in *days*, first and last day each."""
    runs: list[tuple[date, date]] = []
    for day in sorted(days):
        if runs and (day - runs[-1][1]).days == 1:
            runs[-1] = (runs[-1][0], day)
        else:
            runs.append((day, day))
    return runs


def _local_day(moment: object) -> date | None:
    """The Vienna date of a parsed ``starts_at``/``ends_at``, or None."""
    return _to_utc(moment).astimezone(_VIENNA_TZ).date() if isinstance(moment, datetime) else None


def _past_first_week(first: date | None, last: date | None, today: date) -> bool:
    """Whether *today* lies behind the first week of a closure of more than four weeks.

    *first* and *last* bound the unbroken run of closure days around
    *today*: the days of the lead sentence (:func:`_closure_run`), or the
    item's own period where the text names none ("von 07.09.2026 bis Ende
    Oktober 2027" gives no last day the reader takes). A run without a known
    end counts as long as it has lasted: on its 29th day it is a closure of
    more than four weeks. A run without a known first day is left alone.

    Operator decision 2026-11-12: "Vorrang nur in der ersten Woche" (module
    docstring). The first week is the first seven days of the run; a closure
    that enters the feed later than that never leads the notices.
    """
    if first is None:
        return False
    length = ((last or today) - first).days + 1
    return length > _LONG_CLOSURE_DAYS and (today - first).days >= _LONG_CLOSURE_FRONT_DAYS


def _oebb_closure_frame(
    item: FeedItem, now_utc: datetime
) -> dict[date, time | None] | tuple[date | None, date | None] | None:
    """What a running ÖBB measure closes: its closure days, or its period, or None.

    None unless *item* is an ÖBB measure that has begun and not ended
    (``starts_at``/``ends_at``) and ranks as a closure. Its lead is the
    sentence the feed shows first (``_oebb_shown_sentences``). When the
    display moved a later section to the front and that section names no
    date, the days come from ÖBB's first sentence, where ÖBB writes them:
    under "REX 3/REX 1: Wien Hauptbahnhof ↔ Deutsch Wagram" (cache of
    04.09.2026) the feed showed "REX 1-Züge werden zwischen Deutsch Wagram
    Bahnhof und Wien Hbf über Wien Stadlau Bahnhst umgeleitet.", a diversion.

    A lead with days counts only when it closes the line
    (:func:`_oebb_closes_line`), and gives its :func:`_oebb_closure_days`.
    A text that names no day at all counts on every day: an unplanned
    disruption ("Wegen einer Oberleitungsstörung sind … Zugfahrten derzeit
    nur eingeschränkt möglich", "Wegen Verlängerung der Bauarbeiten sind …
    bis voraussichtlich 09:00 Uhr keine Fahrten möglich"). A measure whose
    days are written in a form the reader does not take ("in der Nacht von
    14.02. auf 15.02.2026", "von 20.10.2026 auf 21.10.2026") is planned: it
    counts on every day only when it closes the line, and a diversion or
    changed times without a readable day rank like the other notices
    (:func:`_names_measure_day`, independent check 2026-11-11).

    Such a text gives the item's own period, first and last Vienna day,
    as one run of closure days: "von 07.09.2026 bis Ende Oktober 2027 keine
    Züge fahren" names its first day and no last day the reader takes. A
    text that names several days in an unread form ("in den Nächten von
    13.02. auf 14.02. und von 20.02. auf 21.02.2027") closes on some days of
    its period, nobody knows on which: it gives today alone, a run the long
    closure rule never touches.
    """
    if item.get("source") != _OEBB_SOURCE:
        return None
    start = _parse_datetime(item.get("starts_at"))
    if isinstance(start, datetime) and _to_utc(start) > now_utc:
        return None
    end = _parse_datetime(item.get("ends_at"))
    if isinstance(end, datetime) and _to_utc(end) <= now_utc:
        return None
    sentences, shown = _oebb_shown_sentences(item)
    lead = shown[0] if shown else ""
    days = _oebb_closure_days(lead)
    if days is None and sentences and lead != sentences[0]:
        days = _oebb_closure_days(sentences[0])
    if days is not None:
        return days if _oebb_closes_line(lead) else None
    named = _measure_days_named(lead) or _measure_days_named(sentences[0] if sentences else lead)
    if named and not _oebb_closes_line(lead):
        return None
    if len(named) > 1:
        today = _to_utc(now_utc).astimezone(_VIENNA_TZ).date()
        return today, today
    return _local_day(start), _local_day(end)


def _oebb_closure_run(item: FeedItem, now_utc: datetime) -> tuple[date | None, date | None] | None:
    """The unbroken run of closure days around today (Vienna), or None when *item* closes nothing now.

    From :func:`_oebb_closure_frame`: the run of its closure days that holds
    today (:func:`_closure_run`; on a last day with an end time only until
    then), or the item's period where the text names no day, either bound
    None where the item does not give it.
    """
    frame = _oebb_closure_frame(item, now_utc)
    if frame is None or isinstance(frame, tuple):
        return frame
    local = _to_utc(now_utc).astimezone(_VIENNA_TZ)
    today = local.date()
    if today not in frame:
        return None
    until = frame[today]
    if until is not None and local.time() >= until:
        return None
    return _closure_run(frame, today)


def _is_oebb_closure_day(item: FeedItem, now_utc: datetime) -> bool:
    """Whether *item* is an ÖBB closure that leads the notices today (Vienna).

    It closes its line today (:func:`_oebb_closure_run`), and it is not a
    closure of more than four weeks past its first week
    (:func:`_past_first_week`, operator decision 2026-11-12).
    """
    run = _oebb_closure_run(item, now_utc)
    return run is not None and not _past_first_week(*run, _to_utc(now_utc).astimezone(_VIENNA_TZ).date())


def _long_closure_start(item: FeedItem, now_utc: datetime) -> datetime | None:
    """The first day (UTC moment of its Vienna midnight) of a long closure past its first week, else None.

    Such a closure ranks like the other notices, and there it counts from
    the day it began, not from the day it reached the feed
    (``item_meta._recency_sort_key``). "Wien Praterstern ↔ Wien Hauptbahnhof
    / Wien St. Marx" has closed the line since 07.09.2026 but reached the
    feed on 07.11.2026, after the filter had learned its two-station
    endpoint: counted from 07.11. it still stood among the first ten in 163
    of the 283 replayed feed states from 07.11. to 12.11.2026 after it had
    lost its priority, as a notice five days old. A measure announced ahead
    counts from its start anyway (``announced_start``), and a WL stop notice
    that enters through a filter change counts from its publication
    (``_initial_first_seen``, 2026-10-04): each stands where it would stand
    had the feed shown it from the beginning.

    Every run of the item counts that is long and past its first week,
    whether or not it closes the line at this hour: the moment does not jump
    back to the day the item reached the feed when the closure pauses or
    ends in the morning of its last day. Of several such runs the earliest.
    """
    frame = _oebb_closure_frame(item, now_utc)
    if frame is None:
        return None
    today = _to_utc(now_utc).astimezone(_VIENNA_TZ).date()
    runs = [frame] if isinstance(frame, tuple) else _closure_runs(frame)
    starts = [first for first, last in runs if first is not None and _past_first_week(first, last, today)]
    if not starts:
        return None
    first = min(starts)
    return _to_utc(datetime(first.year, first.month, first.day, tzinfo=_VIENNA_TZ))
