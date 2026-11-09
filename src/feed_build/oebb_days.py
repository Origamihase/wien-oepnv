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

The days come from the lead sentence, the closure the feed shows: ÖBB adds
further sections for single trains ("HALTAUSFALL: Von 11.09.2026 bis
09.10.2026 … kann der Zug RJ 263 in Wien Hütteldorf … nicht halten."), and
their days would put a text about another week in front ("Hbf ↔
Westbahnhof … von 23.10. bis 27.10. keine Fernverkehrszüge" on 29.10.).
The lead sentence gives its days by the dates ÖBB writes ("von 03.11.2026 bis 10.11.2026",
"am 25.10.2026 und 01.11.2026", "von 23.10.2026 (23:00 Uhr) bis 27.10.2026
(04:00 Uhr)"), narrowed by a weekday window ("(jeweils von Samstag 23:15 Uhr
bis Sonntag 04:40 Uhr und von Sonntag 23:15 Uhr bis Montag 04:40 Uhr)" gives
Saturday and Sunday, the evenings the closure begins). A text without a
readable date (an unplanned disruption "bis voraussichtlich 09:00 Uhr",
"bis Ende Oktober 2027") counts on every day of the item's own period.
"""

from __future__ import annotations

import html
import re
from datetime import date, datetime, time, timedelta

from ..feed_types import FeedItem
from .base import _OEBB_SOURCE, _SENTENCE_SPLIT_RE, _VIENNA_TZ, _parse_datetime, _to_utc

__all__ = ["_oebb_closure_days", "_is_oebb_closure_day"]

_DATE = r"(\d{1,2})\.(\d{2})\.(\d{4})"
_TIME = r"(?:\s*\(\d{1,2}:\d{2} Uhr\))?"
_RANGE_RE = re.compile(rf"\bvon {_DATE}{_TIME} bis {_DATE}(?:\s*\((\d{{1,2}}):(\d{{2}}) Uhr\))?")
_SINGLE_RE = re.compile(rf"\b(?:am|und) {_DATE}")
# ÖBB breaks digits with markup: "von <b>2</b>4.10.2026", "am 25 .10.2026".
_SPLIT_DAY_RE = re.compile(r"(?<!\d)(\d) (\d)\.(\d{2}\.\d{4})")
_SPACE_DOT_RE = re.compile(r"(\d) \.(\d{2}\.\d{4})")
_WINDOW_RE = re.compile(r"\(jeweils ([^()]*)\)")
_WEEKDAYS = {
    "montag": 0, "dienstag": 1, "mittwoch": 2, "donnerstag": 3,
    "freitag": 4, "samstag": 5, "sonntag": 6,
}
_WINDOW_START_RE = re.compile(r"\bvon (" + "|".join(_WEEKDAYS) + r")\b", re.IGNORECASE)
_WINDOW_DAY_RE = re.compile(r"\b(" + "|".join(_WEEKDAYS) + r")\b", re.IGNORECASE)
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


def _oebb_closure_days(description: str) -> dict[date, time | None] | None:
    """The days the lead sentence says the line is closed, or None if it names none.

    Each day maps to the clock time the closure ends on it, or None for the
    whole day: "bis 27.10.2026 (04:00 Uhr)" closes 27.10. only until 04:00,
    and a text about a closure that ended at 04:00 must not lead that day.
    """
    text = _SENTENCE_SPLIT_RE.split(_plain(description).strip(), maxsplit=1)[0]
    days: dict[date, time | None] = {}

    def add(day: date, until: time | None = None) -> None:
        if day in days and days[day] is None:
            return
        earlier = days.get(day)
        days[day] = None if until is None else max(until, earlier) if earlier else until

    for match in _RANGE_RE.finditer(text):
        first, last = _day(match, 1), _day(match, 4)
        if first is None or last is None or last < first or (last - first).days > 400:
            continue
        for n in range((last - first).days):
            add(first + timedelta(days=n))
        until = None
        if match.group(7) is not None and int(match.group(7)) < 24:
            until = time(int(match.group(7)), int(match.group(8)))
        add(last, until)
    ranged = _RANGE_RE.sub(" ", text)
    for match in _SINGLE_RE.finditer(ranged):
        single = _day(match, 1)
        if single is not None:
            add(single)
    if not days:
        return None
    weekdays = _window_weekdays(text)
    if weekdays is not None:
        days = {day: until for day, until in days.items() if day.weekday() in weekdays}
    return days


def _is_oebb_closure_day(item: FeedItem, now_utc: datetime) -> bool:
    """Whether *item* is an ÖBB measure that closes its line today (Vienna).

    The item must have begun and not ended (``starts_at``/``ends_at``);
    within that, today must be one of :func:`_oebb_closure_days` (on a last
    day with an end time only until then), or the text names no date at all.
    """
    if item.get("source") != _OEBB_SOURCE:
        return False
    start = _parse_datetime(item.get("starts_at"))
    if isinstance(start, datetime) and _to_utc(start) > now_utc:
        return False
    end = _parse_datetime(item.get("ends_at"))
    if isinstance(end, datetime) and _to_utc(end) <= now_utc:
        return False
    days = _oebb_closure_days(str(item.get("description") or ""))
    if days is None:
        return True
    local = _to_utc(now_utc).astimezone(_VIENNA_TZ)
    if local.date() not in days:
        return False
    until = days[local.date()]
    return until is None or local.time() < until
