"""The time line of an item ("seit", "bis", weekdays) and when an incident started.

Moved verbatim from ``src/build_feed.py`` (700-line rule, Etappe 8); ``src.build_feed``
re-exports every name that src/, scripts/ or the tests read from it.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from ..feed_types import FeedItem
from ..feed import config as feed_config
from ..providers.wl_plausibility import incident_begin
from ..utils.text import html_to_text

from .wl_tickers import _incident_cause
from .base import _OEBB_SOURCE, _PLANNED_DISRUPTION_RE, _VIENNA_TZ, _parse_datetime, _parse_state_time, _to_utc, log

__all__ = [
    "_time_line_day", "_plausible_end", "format_local_times", "_MEASURED_SOURCES", "_first_published",
    "_incident_since", "_is_all_clear", "_wl_incident_start", "_shown_since",
]


# Joins the words of a time line so a display never breaks it in two. A full
# NO-BREAK SPACE (U+00A0): the narrow one (U+202F) used until 2026-10-06 set
# the words so close on the TV that "[Ab Mi 07.10. bis 28.10.]" read almost
# as one word in the EasySignage preview of 06.10.
_NBSP = "\u00a0"
# Weekday abbreviations for the time line, Monday first like ``weekday()``.
_WEEKDAYS_DE: tuple[str, ...] = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
# Within this many days from today a date carries its weekday ("Mo 05.10.").
# Further out the weekday no longer helps anyone plan the next few days.
_TIME_LINE_WEEKDAY_DAYS = 6


def _time_line_day(when: datetime, today: date) -> str:
    """``when``'s day for the time line: "05.10.", "Mo 05.10.", "05.10.2027".

    The year appears only when it is not the current one, the weekday only
    for the coming week (see ``_TIME_LINE_WEEKDAY_DAYS``).
    """
    text = f"{when:%d.%m.}"
    if when.year != today.year:
        text += f"{when:%Y}"
    if 0 <= (when.date() - today).days <= _TIME_LINE_WEEKDAY_DAYS:
        text = f"{_WEEKDAYS_DE[when.weekday()]} {text}"
    return text


def _plausible_end(
    start_local: datetime | None, end_local: datetime | None, now_local: datetime
) -> datetime | None:
    """``end_local``, or ``None`` when it lies before the start or absurdly far out.

    "Absurdly far" is more than ``ABSOLUTE_MAX_AGE_DAYS`` (default 540) past
    today, or past the start when that is still ahead. Measured from the
    start alone, a long-running item lost a near, plausible end: "N8:
    Thaliastraße U" read "Seit 24.07.2024" on 2026-10-02 although WL named
    16.11.2026. (That end is WL's 11:11 expiry date, and since 2026-10-08
    the line leaves it out on purpose, see
    :func:`src.providers.wl_plausibility.end_unknown`; the rule stays for
    real ends.)
    """
    if end_local is None:
        return None
    # Compared in UTC: two datetimes in the same zone compare by wall clock
    # (see :func:`format_local_times`).
    if start_local is not None and _to_utc(end_local) < _to_utc(start_local):
        log.warning("Enddatum liegt vor Startdatum")
        return None
    anchor = max(start_local, now_local, key=_to_utc) if start_local is not None else now_local
    max_span_days = feed_config.ABSOLUTE_MAX_AGE_DAYS
    if (_to_utc(end_local) - _to_utc(anchor)).days > max_span_days:
        log.warning(
            "Enddatum liegt mehr als %s Tage nach Beginn bzw. heute. Setze Enddatum auf None.",
            max_span_days,
        )
        return None
    return end_local


def format_local_times(
    start: datetime | None,
    end: datetime | None,
    now: datetime | None = None,
    *,
    since: datetime | None = None,
) -> str:
    """The time line of an item, phrased for someone reading it today.

    Words are joined with NO-BREAK SPACE (U+00A0), never a plain space:
    the line must not wrap in the middle on a display.

    The display question is "does this apply now, and until when?", so the
    line says what matters for today instead of two full dates (operator
    decision 2026-10-02)::

        Heute                    running, ends today
        Bis Sa 03.10.            running, ends later (start is past, irrelevant)
        Seit 30.09.              running, no end
        Am So 04.10.             one day, not today
        Ab Mo 05.10. bis 11.11.  begins later
        Ab Mo 05.10.             begins later, no end
        Bis 16.11.               no start

    "Heute" is relative: the feed is rebuilt every 30 minutes, and an item
    that ended yesterday has left the feed by the first build after
    midnight. No clock time for an end: WL's own ends for incidents are
    often exactly one hour after the start, a default rather than a forecast.

    ``since`` is the begin of an unplanned disruption (see
    :func:`_incident_since`). When it lies earlier today, the line says
    when it began, so a reader can tell how old the incident is (operator
    wish 2026-10-03)::

        Seit 10:37               incident since 10:37 today, ends today or open

    An incident that began on an earlier day, or that its source expects to
    last beyond today, keeps the lines above.

    Days and clock times are Vienna's, the order of two moments is not:
    Python compares two datetimes in the same zone by their wall clock and
    ignores the offset. In the hour that repeats when summer time ends
    (25.10.2026, 02:00 to 03:00 twice), an accident at 02:26 summer time
    read as later than the build at 02:01 winter time, 35 minutes after it,
    and the line fell back to "Heute" (a real night shifted onto that date).
    Moments are therefore compared in UTC.
    """
    now_local = _to_utc(now).astimezone(_VIENNA_TZ) if now else datetime.now(_VIENNA_TZ)
    today = now_local.date()
    start_local = _to_utc(start).astimezone(_VIENNA_TZ) if isinstance(start, datetime) else None
    end_local = _to_utc(end).astimezone(_VIENNA_TZ) if isinstance(end, datetime) else None
    end_local = _plausible_end(start_local, end_local, now_local)
    since_local = _to_utc(since).astimezone(_VIENNA_TZ) if isinstance(since, datetime) else None
    if (
        since_local is not None
        and since_local.date() == today
        and _to_utc(since_local) <= _to_utc(now_local)
        and (end_local is None or end_local.date() == today)
    ):
        return f"Seit {since_local:%H:%M}".replace(" ", _NBSP)
    return _time_line_text(start_local, end_local, today).replace(" ", _NBSP)


def _scheduled_clock(when: datetime) -> bool:
    """Whether ``when`` sits on a full hour, the mark of a scheduled entry.

    WL's system switches pre-entered measures on at the full hour, a few
    seconds late ("12: Betrieb ab Franz-Josefs-Bahnhof" 04:00:16, "N41:
    Busse halten …" 01:00:15), and display tickers reused for a new
    incident keep such a start from days before ("10A: Fahrtbehinderung"
    with 09.07. 00:00:25 on 11.07.). An incident begins on a full hour about
    once in sixty; :func:`_incident_since` lets a disruption whose cause
    names an incident keep it (operator decision 2026-11-03).
    """
    return _to_utc(when).astimezone(_VIENNA_TZ).minute == 0


# Sources whose start is measured, not entered by hand: the Stammstrecke
# monitor (``src.feed.stammstrecke.EVENT_SOURCE``) dates an episode by the
# first delayed departure, and a departure at the full hour is no schedule.
_MEASURED_SOURCES: frozenset[str] = frozenset({"VOR/VAO"})


def _first_published(it: FeedItem, entry: dict[str, Any] | None) -> datetime | None:
    """The minute ÖBB first published the message ``it``, kept in ``entry``.

    ÖBB re-publishes a message under the same GUID with every update, and
    the ``pubDate`` moves with it: "Wien Floridsdorf ↔ Wien Praterstern"
    (Weichenstörung, 27.09.2026) came at 15:48, 17:26 and 20:07, as did 21
    of the 33 ÖBB disruptions cached from 12.09. to 02.10.2026. The earliest
    ``pubDate`` seen is stored in the item's state entry as
    ``first_published``. An entry from before that field starts from the
    earlier of ``pubDate`` and ``first_seen``: the build saw the message no
    earlier than ÖBB published it. ``None`` for other sources.
    """
    if it.get("source") != _OEBB_SOURCE:
        return None
    pub = _parse_datetime(it.get("pubDate"))
    if not isinstance(pub, datetime):
        return None
    earliest = _to_utc(pub)
    if entry is None:
        return earliest
    known = _parse_state_time(entry, "first_published")
    if known is None:
        known = _parse_state_time(entry, "first_seen")
    if known is not None and known < earliest:
        earliest = known
    entry["first_published"] = earliest.isoformat()
    return earliest


def _incident_since(
    it: FeedItem, starts_at: datetime | None, first_published: datetime | None = None
) -> datetime | None:
    """When the unplanned disruption ``it`` began, or ``None``.

    Every source files its disruptions under "Störung": WL ``trafficInfos``,
    ÖBB and the Stammstrecke monitor. Planned ones
    (:data:`_PLANNED_DISRUPTION_RE`) and reports of a disruption that is
    already over (:func:`_reports_past_disruption`) do not qualify.

    One WL item often bundles several WL messages about the incident.
    ``pubDate`` is the earliest of them, ``starts_at`` the latest
    (``wl_fetch`` keeps the latest so a reused stale ticker cannot date the
    item back). On 2026-10-03 "86A/87A/95A: Fahrtbehinderung wegen
    Rettungseinsatz" carried ``pubDate`` 10:37:00 — the minute WL entered
    the incident — and per-line tickers from 10:42:44 to 10:54:13 as
    ``starts_at``. The earliest time is the begin, as long as it is from the
    same Vienna day and not a scheduled full hour (:func:`_scheduled_clock`),
    the marks of the stale tickers seen since July. A disruption whose cause
    names an incident (:func:`wl_tickers._incident_cause`: "U2: Verspätungen
    … Grund: Schadhaftes Fahrzeug") keeps its full hour: an incident can be
    reported on one (operator decision 2026-11-03); measures switched on at
    the full hour ("71E: Ersatzverkehr Busse halten …") keep "Heute". ÖBB's begin is the
    minute it first published the message, ``first_published``
    (:func:`_first_published`): its ``starts_at`` is the date the
    message's validity begins (00:00, since 2026-09-12), and its
    ``pubDate`` moves on with every update; a full hour counts there too.
    The Stammstrecke monitor's begin is the first delayed departure it measured
    (:data:`_MEASURED_SOURCES`).
    """
    if it.get("category") != "Störung" or not isinstance(starts_at, datetime):
        return None
    text = f"{it.get('title') or ''} {html_to_text(str(it.get('description') or ''))}"
    if _PLANNED_DISRUPTION_RE.search(text) or _reports_past_disruption(it, text):
        return None
    if it.get("source") in _MEASURED_SOURCES:
        return starts_at
    pub_date = _parse_datetime(it.get("pubDate"))
    if it.get("source") == _OEBB_SOURCE and isinstance(pub_date, datetime):
        # No full-hour rule here: it marks WL's pre-entered measures, and
        # ÖBB publishes by hand. The two ÖBB disruptions since July first
        # published on a full hour were real incidents ("Wien Meidling ↔ Wien
        # Liesing", Polizeieinsatz, 03.10.2026 20:00:01), and that one read
        # "Heute" while the line was closed.
        return first_published or pub_date
    pub_date = incident_begin(pub_date, _wl_incident_start(it))
    since = starts_at
    if (
        isinstance(pub_date, datetime)
        and pub_date <= starts_at
        and _to_utc(pub_date).astimezone(_VIENNA_TZ).date()
        == _to_utc(starts_at).astimezone(_VIENNA_TZ).date()
        and not _scheduled_clock(pub_date)
    ):
        since = pub_date
    # An incident cause beats the full hour of the begin (operator decision
    # 2026-11-03, "[Seit hh:00] bringt mehr Infos"): "N71: Polizeieinsatz"
    # from 02:00:25. An earlier ``pubDate`` on a full hour still does not
    # date the item back: that is the mark of a reused stale ticker.
    return since if not _scheduled_clock(since) or _incident_cause(it) else None


def _shown_since(it: FeedItem, since: datetime | None) -> datetime | None:
    """The begin the time line shows for ``since`` (:func:`_incident_since`).

    WL enters a disruption that runs over midnight anew at 00:00 Vienna time,
    a few seconds after the full hour: "31: Wasserrohrbruch Betrieb ab
    Wexstraße", first published 02.09.2026 06:43, came back with 03.09.
    00:00:15 and read "[Seit 00:00]" all day on 03.09.; the 2, 18, 62 and O
    did the same on 03.09. and 05.09., "25: Hitzeschäden" on 03.07. Since the
    incident cause beats the full hour (2026-11-03), such a start keeps the
    line without a clock time ("[Heute]", "[Seit heute]"), as before; the item
    still ranks as a running disruption. ÖBB (published by hand) and the
    Stammstrecke monitor (measured) keep their begin.
    """
    if since is None or it.get("source") == _OEBB_SOURCE or it.get("source") in _MEASURED_SOURCES:
        return since
    local = _to_utc(since).astimezone(_VIENNA_TZ)
    return None if (local.hour, local.minute) == (0, 0) else since


# ÖBB reports a disruption that is already over in the past tense: "Wegen
# eines Polizeieinsatzes waren in Mödling Bahnhof bis 19:55 Uhr keine Fahrten
# möglich", published 19:57. 60 of the 193 ÖBB disruptions cached since July
# are such reports or an "Aufhebung"; "Seit 19:57" would claim the opposite.
_PAST_DISRUPTION_RE: re.Pattern[str] = re.compile(
    r"\bwaren\b.{0,160}?\bbis\s+\d{1,2}[:.]\d{2}\s*Uhr"
)


def _reports_past_disruption(it: FeedItem, text: str) -> bool:
    """Whether ``it`` reports the end of a disruption rather than a running one."""
    return _is_all_clear(it) or _PAST_DISRUPTION_RE.search(text) is not None


def _time_line_text(start_local: datetime | None, end_local: datetime | None, today: date) -> str:
    """The words of :func:`format_local_times`, joined with plain spaces."""

    if start_local is not None and start_local.date() > today:
        first = _time_line_day(start_local, today)
        if end_local is None:
            return f"Ab {first}"
        if end_local.date() == start_local.date():
            return f"Am {first}"
        return f"Ab {first} bis {_time_line_day(end_local, today)}"
    if end_local is not None:
        if end_local.date() == today:
            return "Heute"
        if start_local is not None and start_local.date() == end_local.date():
            return f"Am {_time_line_day(end_local, today)}"
        return f"Bis {_time_line_day(end_local, today)}"
    if start_local is not None:
        if start_local.date() == today:
            return "Seit heute"
        return f"Seit {_time_line_day(start_local, today)}"
    return ""


# An all-clear title: "Aufhebung Verkehrseinschränkung: …" or "Aufhebung
# Streckenunterbrechung: …", optionally behind a line label ("REX 50: …") or
# a raw ÖBB update prefix ("Update 5 (25.09.2026 10:48) …") that a cleaning
# step missed. ``\b`` keeps "Aufhebungen" and similar words out.
_ALL_CLEAR_TITLE_RE = re.compile(
    r"^\s*(?:Update\s+\d+\s*\([^)]*\)\s*[:\-–]?\s*)?(?:[^:]{1,40}:\s*)?Aufhebung\b",
    re.IGNORECASE,
)


def _is_all_clear(item: FeedItem) -> bool:
    """Whether ``item`` reports the end of a disruption rather than one."""
    return bool(_ALL_CLEAR_TITLE_RE.match(str(item.get("title") or "")))


def _wl_incident_start(item: FeedItem) -> datetime | None:
    """The earliest start of *item*'s WL incident messages (``wl_fetch``), if it has one."""
    return _parse_datetime(item.get("_incident_start"))
