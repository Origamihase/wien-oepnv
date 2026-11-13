"""A closure of more than four weeks leads the notices only in its first week.

Operator decision 2026-11-12, card "Soll die Dauersperre
Praterstern–Hauptbahnhof bis Oktober 2027 täglich vorn stehen?": "Vorrang nur
in der ersten Woche". Live case: "Wien Praterstern ↔ Wien Hauptbahnhof / Wien
St. Marx" (07.09.2026 to 31.10.2027) ranked as a closure on every day
(decision 2026-11-08) and held one of the ten places in 267 of the 283
published feed states from 07.11. to 12.11.2026.

The rule looks at the unbroken run of closure days around today: weekend
nights and closures of up to four weeks keep the front on every closure day.
Past its first week the long closure stands among the notices and counts
there from the day it began. A current incident keeps its place. The slot of
an ÖBB title: ``tests/test_oebb_long_closure_title_slot.py``.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

import src.build_feed as bf
from src.feed_build import oebb_days
from src.feed_build.oebb_days import (
    _closure_run,
    _is_oebb_closure_day,
    _long_closure_start,
    _oebb_closure_days,
    _past_first_week,
)
from src.feed_types import FeedItem
from src.utils.cache import write_cache
from src.utils.serialize import serialize_for_cache
from tests._conftest.build_feed_bindings import setattr_all

# ÖBB RSS, cache of 12.11.2026 (shortened behind the lead sentence).
PRATERSTERN = (
    "07.09.2026 - 31.10.2027<br/><br/>Wegen Bauarbeiten können <br>zwischen <b>Wien Hbf </b>bzw.<b> Wien "
    "St.Marx</b> und <b>Wien Praterstern</b><br>von <b>07.09.2026</b> bis <b>Ende Oktober 2027 </b><br>keine "
    "Züge fahren.<br>Als Ersatz haben Sie die Möglichkeit das Angebot der Wiener Linien zu nutzen."
)
GRAMATNEUSIEDL = (
    "31.10.2026 - 30.11.2026<br/><br/>Wegen Bauarbeiten können <br>zwischen <b>Wien Hbf (U)</b> und "
    "<b>Gramatneusiedl Bahnhof </b><br>von <b>31.10.2026</b> bis <b>30.11.2026 </b><span>(jeweils von "
    "Samstag 23:15 Uhr bis Sonntag 04:40 Uhr und von Sonntag 23:15 Uhr bis Montag 04:40 Uhr)<br></span>"
    "keine Nahverkehrszüge fahren.<br>Ein Schienenersatzverkehr mit Autobussen wird für Sie eingerichtet."
)
VIENNA = ZoneInfo("Europe/Vienna")
NO_END_IN_SIGHT = "Wegen eines Hangrutsches sind zwischen Wien Hütteldorf und Purkersdorf derzeit keine Fahrten möglich."


def _noon(day: date) -> datetime:
    """11:00 UTC on *day*: noon or 13:00 in Vienna, far from both midnights."""
    return datetime(day.year, day.month, day.day, 11, 0, tzinfo=UTC)


def _oebb(description: str, start: date, end: date | None, title: str = "Wien Praterstern ↔ Wien Hauptbahnhof") -> FeedItem:
    return {
        "source": "ÖBB", "category": "Störung", "title": title, "description": description,
        "guid": f"oebb-{title}", "link": "https://fahrplan.oebb.at/", "pubDate": _noon(start - timedelta(days=3)),
        "starts_at": datetime(start.year, start.month, start.day, tzinfo=VIENNA),
        "ends_at": None if end is None else datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=VIENNA),
    }


def _dated(first: date, last: date) -> str:
    """A closure whose lead sentence writes both days, as ÖBB does for most works."""
    return (
        f"{first:%d.%m.%Y} - {last:%d.%m.%Y}<br/><br/>Wegen Bauarbeiten können <br>zwischen <b>Wien Meidling</b> "
        f"und <b>Wien Hütteldorf</b><br>von <b>{first:%d.%m.%Y}</b> bis <b>{last:%d.%m.%Y}</b><br>keine Züge fahren."
    )


# --- The live case ----------------------------------------------------------


def test_praterstern_names_no_day_the_reader_takes() -> None:
    # "bis Ende Oktober 2027" is no date: the closure counts on every day of
    # the item's own period, and that period bounds its run.
    assert _oebb_closure_days(PRATERSTERN.split("<br/><br/>", 1)[1]) is None


@pytest.mark.parametrize(
    ("day", "leads"),
    [
        (date(2026, 9, 7), True),  # first day
        (date(2026, 9, 13), True),  # seventh day
        (date(2026, 9, 14), False),  # eighth day
        (date(2026, 11, 12), False),  # the day of the decision
        (date(2027, 10, 31), False),  # last day
    ],
)
def test_praterstern_leads_only_in_its_first_week(day: date, leads: bool) -> None:
    item = _oebb(PRATERSTERN, date(2026, 9, 7), date(2027, 10, 31))
    assert _is_oebb_closure_day(item, _noon(day)) is leads


def _notice(title: str, guid: str, first_seen: datetime) -> FeedItem:
    return {
        "source": "Wiener Linien", "category": "Hinweis", "title": title, "description": f"{title}.", "guid": guid,
        "link": "https://www.wienerlinien.at/betriebsinfo", "pubDate": first_seen, "starts_at": first_seen, "ends_at": None,
    }


def test_praterstern_stands_among_the_notices_from_the_day_it_began() -> None:
    # The closure reached the feed on 07.11.2026, two months after it began
    # (the filter learned its two-station endpoint). Counted from 07.11. it
    # stood among the first ten in 163 of 283 replayed feed states after it
    # had lost its priority. It counts from 07.09., the day it began.
    now = _noon(date(2026, 11, 12))
    closure = _oebb(PRATERSTERN, date(2026, 9, 7), date(2027, 10, 31))
    october = _notice("5B: Bauarbeiten", "wl-5b", _noon(date(2026, 10, 20)))
    august = _notice("31: Wexstraße", "wl-31", _noon(date(2026, 8, 20)))
    state: dict[str, dict[str, Any]] = {
        str(closure["guid"]): {"first_seen": "2026-11-07T12:31:01+00:00"},
        "wl-5b": {"first_seen": _noon(date(2026, 10, 20)).isoformat()},
        "wl-31": {"first_seen": _noon(date(2026, 8, 20)).isoformat()},
    }
    items = [august, closure, october]
    items.sort(key=lambda it: bf._recency_sort_key(it, state, now))
    assert [it["title"] for it in items] == ["5B: Bauarbeiten", closure["title"], "31: Wexstraße"]
    key = bf._recency_sort_key(closure, state, now)
    assert key[0] == 2
    assert key[1] == -datetime(2026, 9, 7, tzinfo=VIENNA).timestamp()


def test_start_of_a_long_closure_is_named_only_past_its_first_week() -> None:
    closure = _oebb(PRATERSTERN, date(2026, 9, 7), date(2027, 10, 31))
    assert _long_closure_start(closure, _noon(date(2026, 9, 13))) is None
    assert _long_closure_start(closure, _noon(date(2026, 9, 14))) == datetime(2026, 9, 6, 22, 0, tzinfo=UTC)
    weekend = _oebb(GRAMATNEUSIEDL, date(2026, 10, 31), date(2026, 11, 30), "Wien Hauptbahnhof ↔ Gramatneusiedl")
    assert _long_closure_start(weekend, _noon(date(2026, 11, 28))) is None
    assert _long_closure_start(_notice("5B: Bauarbeiten", "wl-5b", _noon(date(2026, 8, 1))), _noon(date(2026, 11, 12))) is None


def test_a_closure_seen_before_it_began_keeps_its_earlier_moment() -> None:
    # The cap only moves a moment back: an entry older than the first day
    # of the closure stays as it is.
    now = _noon(date(2026, 11, 12))
    closure = _oebb(PRATERSTERN, date(2026, 9, 7), date(2027, 10, 31))
    state: dict[str, dict[str, Any]] = {str(closure["guid"]): {"first_seen": "2026-09-04T09:10:05+00:00"}}
    assert bf._recency_sort_key(closure, state, now)[1] == -datetime(2026, 9, 4, 9, 10, 5, tzinfo=UTC).timestamp()


def test_in_its_first_week_the_closure_leads_the_notices() -> None:
    now = _noon(date(2026, 9, 10))
    closure = _oebb(PRATERSTERN, date(2026, 9, 7), date(2027, 10, 31))
    fresh = _notice("5B: Bauarbeiten", "wl-5b", now - timedelta(hours=3))
    state: dict[str, dict[str, Any]] = {
        str(closure["guid"]): {"first_seen": "2026-09-04T09:10:05+00:00"},
        "wl-5b": {"first_seen": (now - timedelta(hours=3)).isoformat()},
    }
    items = [fresh, closure]
    items.sort(key=lambda it: bf._recency_sort_key(it, state, now))
    assert [it["title"] for it in items] == [closure["title"], "5B: Bauarbeiten"]


# --- What keeps the front ---------------------------------------------------


def test_weekend_nights_over_a_month_keep_the_front_in_every_closure_night() -> None:
    # 31.10. to 30.11. is longer than four weeks, but the closure days are
    # runs of two (Saturday, Sunday): the fifth weekend leads like the first.
    item = _oebb(GRAMATNEUSIEDL, date(2026, 10, 31), date(2026, 11, 30), "Wien Hauptbahnhof ↔ Gramatneusiedl")
    days = _oebb_closure_days(GRAMATNEUSIEDL.split("<br/><br/>", 1)[1])
    assert days is not None
    assert _closure_run(days, date(2026, 11, 28)) == (date(2026, 11, 28), date(2026, 11, 29))
    for day in (date(2026, 10, 31), date(2026, 11, 14), date(2026, 11, 28), date(2026, 11, 29)):
        assert _is_oebb_closure_day(item, _noon(day)), day
    assert not _is_oebb_closure_day(item, _noon(date(2026, 11, 25)))  # a Wednesday


def test_a_closure_of_four_weeks_keeps_the_front_throughout() -> None:
    first, last = date(2027, 2, 1), date(2027, 2, 28)  # 28 days
    item = _oebb(_dated(first, last), first, last)
    assert all(_is_oebb_closure_day(item, _noon(first + timedelta(days=n))) for n in range(28))


def test_a_closure_of_four_weeks_and_a_day_leads_seven_days() -> None:
    first, last = date(2027, 2, 1), date(2027, 3, 1)  # 29 days
    item = _oebb(_dated(first, last), first, last)
    leads = [_is_oebb_closure_day(item, _noon(first + timedelta(days=n))) for n in range(29)]
    assert leads == [True] * 7 + [False] * 22


def test_two_runs_in_one_sentence_are_judged_each_on_its_own() -> None:
    # Five weeks, a pause of three days, one more week: the second run is
    # short and leads on each of its days; the first only in its first week.
    text = (
        "Wegen Bauarbeiten können zwischen Wien Meidling und Wien Hütteldorf von 01.02.2027 bis 07.03.2027 "
        "und von 11.03.2027 bis 17.03.2027 keine Züge fahren."
    )
    item = _oebb(text, date(2027, 2, 1), date(2027, 3, 17))
    assert _is_oebb_closure_day(item, _noon(date(2027, 2, 7)))
    assert not _is_oebb_closure_day(item, _noon(date(2027, 2, 8)))
    assert not _is_oebb_closure_day(item, _noon(date(2027, 3, 7)))
    assert not _is_oebb_closure_day(item, _noon(date(2027, 3, 9)))  # the pause
    assert _is_oebb_closure_day(item, _noon(date(2027, 3, 11)))
    assert _is_oebb_closure_day(item, _noon(date(2027, 3, 17)))


# --- A closure without a known end ------------------------------------------


@pytest.mark.parametrize(("running_days", "leads"), [(1, True), (8, True), (28, True), (29, False), (90, False)])
def test_a_closure_without_an_end_counts_as_long_as_it_has_lasted(running_days: int, leads: bool) -> None:
    # Without an end nobody knows on day 8 that the closure will outlast four
    # weeks; from its 29th day it has.
    start = date(2027, 1, 4)
    item = _oebb(NO_END_IN_SIGHT, start, None)
    assert _is_oebb_closure_day(item, _noon(start + timedelta(days=running_days - 1))) is leads


def test_a_run_without_a_known_first_day_is_left_alone() -> None:
    assert not _past_first_week(None, date(2027, 10, 31), date(2026, 11, 12))
    item = _oebb(NO_END_IN_SIGHT, date(2027, 1, 4), None)
    item["starts_at"] = None
    assert _is_oebb_closure_day(item, _noon(date(2027, 6, 1)))


@pytest.mark.parametrize(
    ("first", "last", "today", "past"),
    [
        (date(2027, 2, 1), date(2027, 2, 28), date(2027, 2, 28), False),  # 28 days
        (date(2027, 2, 1), date(2027, 3, 1), date(2027, 2, 7), False),  # 29 days, day 7
        (date(2027, 2, 1), date(2027, 3, 1), date(2027, 2, 8), True),  # 29 days, day 8
        (date(2027, 2, 1), None, date(2027, 2, 28), False),  # open, day 28
        (date(2027, 2, 1), None, date(2027, 3, 1), True),  # open, day 29
    ],
)
def test_bounds_of_the_first_week(first: date, last: date | None, today: date, past: bool) -> None:
    assert _past_first_week(first, last, today) is past


# --- The day changes at Vienna midnight -------------------------------------


def test_first_week_ends_at_vienna_midnight() -> None:
    # 13.09.2026 is the seventh day. 23:30 Vienna is 21:30 UTC; 00:30 Vienna
    # on 14.09. is still 13.09. in UTC.
    closure = _oebb(PRATERSTERN, date(2026, 9, 7), date(2027, 10, 31))
    assert _is_oebb_closure_day(closure, datetime(2026, 9, 13, 23, 30, tzinfo=VIENNA).astimezone(UTC))
    after = datetime(2026, 9, 14, 0, 30, tzinfo=VIENNA).astimezone(UTC)
    assert after.date() == date(2026, 9, 13)
    assert not _is_oebb_closure_day(closure, after)
    assert _long_closure_start(closure, after) == datetime(2026, 9, 7, tzinfo=VIENNA)


def test_the_closure_must_have_begun_and_not_ended() -> None:
    closure = _oebb(PRATERSTERN, date(2026, 9, 7), date(2027, 10, 31))
    assert not _is_oebb_closure_day(closure, _noon(date(2026, 9, 6)))
    assert _long_closure_start(closure, _noon(date(2026, 9, 6))) is None
    assert _long_closure_start(closure, _noon(date(2027, 11, 1))) is None


# --- The start of a long closure, every path --------------------------------


def test_a_dated_long_closure_counts_from_its_first_day() -> None:
    first, last = date(2027, 2, 1), date(2027, 3, 7)  # 35 days, both days written
    closure = _oebb(_dated(first, last), first, last)
    assert _long_closure_start(closure, _noon(date(2027, 2, 7))) is None
    assert _long_closure_start(closure, _noon(date(2027, 2, 8))) == datetime(2027, 2, 1, tzinfo=VIENNA)
    # It reached the feed on 20.02.: among the notices it counts from 01.02.
    state: dict[str, dict[str, Any]] = {str(closure["guid"]): {"first_seen": "2027-02-20T08:00:00+00:00"}}
    key = bf._recency_sort_key(closure, state, _noon(date(2027, 2, 25)))
    assert (key[0], key[1]) == (2, -datetime(2027, 2, 1, tzinfo=VIENNA).timestamp())


def test_a_closure_without_an_end_counts_from_its_first_day_once_it_is_long() -> None:
    start = date(2027, 1, 4)
    closure = _oebb(NO_END_IN_SIGHT, start, None)
    assert _long_closure_start(closure, _noon(start + timedelta(days=27))) is None
    assert _long_closure_start(closure, _noon(start + timedelta(days=28))) == datetime(2027, 1, 4, tzinfo=VIENNA)


def test_the_start_holds_when_the_closure_ends_in_the_morning_or_pauses() -> None:
    # Ends on its last day at 04:00, pauses three days, runs one more week.
    text = (
        "Wegen Bauarbeiten können zwischen Wien Meidling und Wien Hütteldorf von 01.02.2027 (23:00 Uhr) bis "
        "07.03.2027 (04:00 Uhr) und von 11.03.2027 bis 17.03.2027 keine Züge fahren."
    )
    closure = _oebb(text, date(2027, 2, 1), date(2027, 3, 17))
    begun = datetime(2027, 2, 1, tzinfo=VIENNA)
    last_morning = datetime(2027, 3, 7, 5, 0, tzinfo=VIENNA).astimezone(UTC)
    assert not _is_oebb_closure_day(closure, last_morning)
    assert _long_closure_start(closure, last_morning) == begun
    assert _long_closure_start(closure, _noon(date(2027, 3, 9))) == begun  # the pause
    # The short run leads on its days, and the item keeps the moment of the long one.
    assert _is_oebb_closure_day(closure, _noon(date(2027, 3, 12)))
    assert _long_closure_start(closure, _noon(date(2027, 3, 12))) == begun


def test_of_two_long_runs_the_earlier_one_gives_the_start() -> None:
    text = (
        "Wegen Bauarbeiten können zwischen Wien Meidling und Wien Hütteldorf von 01.02.2027 bis 07.03.2027 "
        "und von 11.03.2027 bis 20.04.2027 keine Züge fahren."
    )
    closure = _oebb(text, date(2027, 2, 1), date(2027, 4, 20))
    assert _long_closure_start(closure, _noon(date(2027, 4, 1))) == datetime(2027, 2, 1, tzinfo=VIENNA)


def test_one_day_without_closure_starts_a_new_run() -> None:
    # 20 days, one day open, 17 days: two short runs, neither is long.
    text = (
        "Wegen Bauarbeiten können zwischen Wien Meidling und Wien Hütteldorf von 01.02.2027 bis 20.02.2027 "
        "und von 22.02.2027 bis 10.03.2027 keine Züge fahren."
    )
    closure = _oebb(text, date(2027, 2, 1), date(2027, 3, 10))
    assert not _is_oebb_closure_day(closure, _noon(date(2027, 2, 21)))
    assert _is_oebb_closure_day(closure, _noon(date(2027, 3, 5)))
    assert _long_closure_start(closure, _noon(date(2027, 3, 5))) is None


# --- Readings of "day after day" --------------------------------------------


def test_a_closure_in_every_night_for_more_than_four_weeks_is_long() -> None:
    # ÖBB, national RSS of 12.11.2026: "Wartberg im Mürztal ↔ St. Marein … von
    # 16.01.2027 bis 08.03.2027 (jeweils 00:30 Uhr - 04:00 Uhr) keine
    # Nahverkehrszüge fahren". Every night without a night between: one run.
    text = (
        "Wegen Bauarbeiten können zwischen Wien Meidling und Wien Hütteldorf von 16.01.2027 bis 08.03.2027 "
        "(jeweils 00:30 Uhr - 04:00 Uhr) keine Nahverkehrszüge fahren."
    )
    closure = _oebb(text, date(2027, 1, 16), date(2027, 3, 8))

    def night(day: date) -> datetime:
        return datetime(day.year, day.month, day.day, 2, 0, tzinfo=VIENNA).astimezone(UTC)

    assert _is_oebb_closure_day(closure, night(date(2027, 1, 22)))  # seventh night
    assert not _is_oebb_closure_day(closure, night(date(2027, 1, 23)))
    assert not _is_oebb_closure_day(closure, night(date(2027, 3, 8)))


def test_several_days_in_a_form_the_reader_does_not_take_are_left_alone() -> None:
    # Three nights within 45 days: nobody knows from the text which days
    # are closed, so the rule of 11.11.2026 stays (every day of the period).
    text = (
        "Wegen Bauarbeiten können zwischen Wien Meidling und Wien Hütteldorf in den Nächten von 13.02. auf "
        "14.02., von 20.02. auf 21.02. und von 28.03. auf 29.03.2027 keine Züge fahren."
    )
    assert _oebb_closure_days(text) is None
    closure = _oebb(text, date(2027, 2, 13), date(2027, 3, 29))
    for day in (date(2027, 2, 13), date(2027, 2, 21), date(2027, 3, 28)):
        assert _is_oebb_closure_day(closure, _noon(day)), day
        assert _long_closure_start(closure, _noon(day)) is None
    # One night names two days, the bound of "several".
    one_night = (
        "Wegen Bauarbeiten können zwischen Wien Meidling und Wien Hütteldorf in der Nacht von 14.02. auf "
        "15.02.2027 keine Züge fahren."
    )
    item = _oebb(one_night, date(2027, 1, 1), date(2027, 3, 31))
    assert _is_oebb_closure_day(item, _noon(date(2027, 3, 1)))
    assert _long_closure_start(item, _noon(date(2027, 3, 1))) is None


def test_an_undated_disruption_over_four_weeks_follows_the_same_rule() -> None:
    # A text without any day counts on every day of its period (rule of
    # 08.11.2026), whatever it says; past the first week of a period of more
    # than four weeks it stands among the notices like the closure.
    text = "Wegen Bauarbeiten sind zwischen Wien Meidling und Wien Hütteldorf Zugfahrten derzeit nur eingeschränkt möglich."
    item = _oebb(text, date(2027, 2, 1), date(2027, 3, 15))
    assert _is_oebb_closure_day(item, _noon(date(2027, 2, 7)))
    assert not _is_oebb_closure_day(item, _noon(date(2027, 2, 8)))


# --- A current incident goes first ------------------------------------------


def test_a_new_disruption_with_a_long_period_is_still_a_current_incident() -> None:
    # "Störungen gehen immer vor.": ÖBB reports a disruption today and dates
    # its period back two weeks and ahead five. New in the feed, it leads
    # as a current incident and counts from now, not from the first day.
    now = _noon(date(2027, 2, 15))
    text = "Wegen Unwetterschäden sind zwischen Wien Meidling und Wien Hütteldorf derzeit keine Fahrten möglich."
    item = _oebb(text, date(2027, 2, 1), date(2027, 3, 20))
    assert _long_closure_start(item, now) is not None
    key = bf._recency_sort_key(item, {}, now)
    assert key[0] == 0
    assert key[1] == -now.timestamp()
    # Two days later it is no current incident any more and stands among the notices.
    later = now + timedelta(days=2)
    state: dict[str, dict[str, Any]] = {str(item["guid"]): {"first_seen": now.isoformat()}}
    key = bf._recency_sort_key(item, state, later)
    assert (key[0], key[1]) == (2, -datetime(2027, 2, 1, tzinfo=VIENNA).timestamp())


def test_a_new_disruption_on_a_closure_day_is_a_current_incident_first() -> None:
    # Without a day in its text it counts on every day of its period, so it is
    # a closure day as well: tier 0 goes before tier 1.
    now = _noon(date(2027, 3, 10))
    text = "Wegen eines Oberleitungsschadens sind zwischen Wien Meidling und Wien Hütteldorf derzeit keine Fahrten möglich."
    item = _oebb(text, date(2027, 3, 10), date(2027, 3, 12))
    assert _is_oebb_closure_day(item, now)
    assert bf._recency_sort_key(item, {}, now)[0] == 0


def test_a_short_run_behind_a_long_one_leads_and_counts_from_the_long_one() -> None:
    # First seen on 25.05., in the middle of the long run: on a day of the
    # short run the item leads, with the moment of the long run's first day.
    text = (
        "Wegen Bauarbeiten können zwischen Wien Meidling und Wien Hütteldorf von 03.05.2027 bis 10.06.2027 "
        "und von 15.06.2027 bis 18.06.2027 keine Züge fahren."
    )
    item = _oebb(text, date(2027, 5, 3), date(2027, 6, 18))
    state: dict[str, dict[str, Any]] = {str(item["guid"]): {"first_seen": _noon(date(2027, 5, 25)).isoformat()}}
    key = bf._recency_sort_key(item, state, _noon(date(2027, 6, 16)))
    assert (key[0], key[1]) == (1, -datetime(2027, 5, 3, tzinfo=VIENNA).timestamp())


def test_ranges_written_latest_first_are_one_run() -> None:
    text = (
        "Wegen Bauarbeiten können zwischen Wien Meidling und Wien Hütteldorf von 25.05.2027 bis 20.06.2027 "
        "und von 03.05.2027 bis 24.05.2027 keine Züge fahren."
    )
    item = _oebb(text, date(2027, 5, 3), date(2027, 6, 20))
    now = _noon(date(2027, 6, 1))
    assert not _is_oebb_closure_day(item, now)
    assert _long_closure_start(item, now) == datetime(2027, 5, 3, tzinfo=VIENNA)


def test_four_weeks_are_counted_in_calendar_days_with_closure() -> None:
    # Monday 22:00 to Monday 04:30 four weeks later lasts 27 days and a few
    # hours and closes on 29 calendar days: long. One day less is not.
    def text(last: date) -> str:
        return (
            "Wegen Bauarbeiten können zwischen Wien Westbahnhof und Wien Hütteldorf von 01.02.2027 (22:00 Uhr) bis "
            f"{last:%d.%m.%Y} (04:30 Uhr) keine S50-Züge fahren."
        )

    long_one = _oebb(text(date(2027, 3, 1)), date(2027, 2, 1), date(2027, 3, 1))
    assert len(_oebb_closure_days(text(date(2027, 3, 1))) or {}) == 29
    assert _is_oebb_closure_day(long_one, _noon(date(2027, 2, 7)))
    assert not _is_oebb_closure_day(long_one, _noon(date(2027, 2, 8)))
    shorter = _oebb(text(date(2027, 2, 28)), date(2027, 2, 1), date(2027, 2, 28))
    assert all(_is_oebb_closure_day(shorter, _noon(date(2027, 2, day))) for day in range(2, 28))


@pytest.mark.parametrize(("sentence", "named"), [
    ("zwischen 06.10.2025 und 16.11.2026 keine Züge halten", {"06.10.2025"}),
    ("zwischen 06.10.2025 (22:00 Uhr) und 16.11.2026 (04:30 Uhr) keine Züge halten", {"06.10.2025"}),
    ("zwischen 06.10.25 und 16.11.26 keine Züge halten", {"06.10.25"}),
    ("zwischen 01.02. und 20.03. keine Züge halten", {"01.02."}),
    # Two ranges are two measures, and a list of days is no range.
    ("zwischen 13.02. und 14.02. und zwischen 20.02. und 21.02. keine Züge halten", {"13.02.", "20.02."}),
    ("am 13.02. und 20.02. keine Züge fahren", {"13.02.", "20.02."}),
    ("in den Nächten auf 14.02.2027 und 21.02.2027 keine Züge fahren", {"14.02.2027", "21.02.2027"}),
    ("zwischen Wien Meidling und Wien Hütteldorf von 20.10.2026 auf 21.10.2026 keine Züge fahren", {"20.10.2026", "21.10.2026"}),
    ("zwischen 22:00 Uhr und 04:30 Uhr am 13.02.2027 keine Züge fahren", {"13.02.2027"}),
    ("zwischen 06.10.2025 und voraussichtlich 16.11.2026 keine Züge halten", {"06.10.2025", "16.11.2026"}),
])
def test_the_second_date_of_a_between_range_is_its_end(sentence: str, named: set[str]) -> None:
    assert oebb_days._measure_days_named(f"Wegen Bauarbeiten können {sentence}.") == named


@pytest.mark.parametrize("wording", [
    "von 06.10.2025 bis 16.11.2026", "zwischen 06.10.2025 und 16.11.2026",
    "zwischen 06.10.2025 (22:00 Uhr) und 16.11.2026 (04:30 Uhr)",
])
def test_a_range_too_long_to_read_falls_under_the_period_of_the_item(wording: str) -> None:
    # More than 400 days are not read as days. The second date of "zwischen …
    # und …" ends the range like one behind "bis": one named day, the period.
    text = f"Wegen Bauarbeiten können zwischen Wien Meidling und Wien Hütteldorf {wording} keine Züge halten."
    assert _oebb_closure_days(text) is None
    item = _oebb(text, date(2025, 10, 6), date(2026, 11, 16))
    assert _is_oebb_closure_day(item, _noon(date(2025, 10, 12)))
    assert not _is_oebb_closure_day(item, _noon(date(2025, 10, 13)))
    assert _long_closure_start(item, _noon(date(2026, 6, 1))) == datetime(2025, 10, 6, tzinfo=VIENNA)
    # A diversion over the same days closes nothing, in either wording.
    diverted = _oebb(text.replace("keine Züge halten", "einige Züge umgeleitet werden"), date(2025, 10, 6), date(2026, 11, 16))
    assert not _is_oebb_closure_day(diverted, _noon(date(2025, 10, 8)))
    assert _long_closure_start(diverted, _noon(date(2026, 6, 1))) is None


# --- The slot of an ÖBB title -----------------------------------------------


def test_a_long_closure_gives_the_slot_of_its_title_to_a_closure_on_its_day() -> None:
    now = _noon(date(2026, 11, 14))
    title = "Wien Praterstern ↔ Wien Hauptbahnhof / Wien St. Marx"
    long_closure = _oebb(PRATERSTERN, date(2026, 9, 7), date(2027, 10, 31), title)
    weekend = _oebb(_dated(date(2026, 11, 14), date(2026, 11, 15)), date(2026, 11, 14), date(2026, 11, 15), title)
    weekend["guid"] = "oebb-weekend"
    december = _oebb(_dated(date(2026, 12, 5), date(2026, 12, 6)), date(2026, 12, 5), date(2026, 12, 6), title)
    december["guid"] = "oebb-december"
    notice = _notice("5B: Bauarbeiten", "wl-5b", _noon(date(2026, 11, 10)))

    def gives_way(item: FeedItem) -> bool:
        return _long_closure_start(item, now) is not None

    def leads(item: FeedItem) -> bool:
        return _is_oebb_closure_day(item, now)

    # The weekend closes today: it holds the slot, the long closure goes behind the field.
    items = [weekend, notice, long_closure]
    assert bf._defer_repeated_route_titles(list(items), gives_way=gives_way, leads=leads) == [weekend, notice, long_closure]
    assert bf._defer_repeated_route_titles([long_closure, notice, weekend], gives_way=gives_way, leads=leads) == [
        notice, weekend, long_closure,
    ]
    # Without the rule the earliest window holds the slot and the weekend goes behind the field.
    assert bf._defer_repeated_route_titles(list(items)) == [notice, long_closure, weekend]
    # A window that does not close today leaves the slot with the long closure.
    assert bf._defer_repeated_route_titles([long_closure, notice, december], gives_way=gives_way, leads=leads) == [
        long_closure, notice, december,
    ]
    # Among long closures the earliest keeps it as well.
    second = _oebb(_dated(date(2026, 10, 1), date(2026, 12, 31)), date(2026, 10, 1), date(2026, 12, 31), title)
    second["guid"] = "oebb-second"
    assert gives_way(second) and not leads(second)
    assert bf._defer_repeated_route_titles([second, notice, long_closure], gives_way=gives_way, leads=leads) == [
        notice, long_closure, second,
    ]


def test_first_week_bounds_are_the_ones_the_operator_chose() -> None:
    assert (oebb_days._LONG_CLOSURE_DAYS, oebb_days._LONG_CLOSURE_FRONT_DAYS) == (28, 7)


# --- Through the real build --------------------------------------------------


def _build_with(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, second: FeedItem | None = None
) -> tuple[list[str], list[str], str]:
    """The real ``main`` over an ÖBB and a WL cache, shaped around the real clock.

    The long closure began 60 days ago and reached the feed 5 days ago; eleven
    WL notices are older in the feed than that and newer than its first day.
    Returns the titles and descriptions of the feed and the closure's title.
    """
    out_file, state_file = tmp_path / "feed.xml", tmp_path / "state.json"
    setattr_all(monkeypatch, bf, "validate_path", lambda path, name: path)
    monkeypatch.setattr(bf.feed_config, "OUT_PATH", out_file)
    monkeypatch.setattr(bf.feed_config, "STATE_FILE", state_file)
    monkeypatch.setattr(bf.feed_config, "FEED_HEALTH_PATH", tmp_path / "feed-health.md")
    monkeypatch.setattr(bf.feed_config, "FEED_HEALTH_JSON_PATH", tmp_path / "feed-health.json")
    monkeypatch.setattr(bf, "refresh_from_env", lambda: None)
    monkeypatch.setenv("KI_ENTSCHEIDUNG", "0")
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    now = datetime.now(UTC).replace(microsecond=0)
    start = (now - timedelta(days=60)).astimezone(VIENNA).date()
    end = (now + timedelta(days=300)).astimezone(VIENNA).date()
    closure = _oebb(_praterstern_like(start, end, f"Ende des Jahres {end.year}"), start, end, _TITLE)
    oebb_items = [closure] if second is None else [closure, second]
    state: dict[str, dict[str, Any]] = {}
    for n, item in enumerate(oebb_items):
        item["_identity"] = f"oebb|{item['guid']}"
        state[str(item["guid"])] = {"first_seen": (now - timedelta(days=5 - n)).isoformat()}
    notices = []
    for n in range(11):
        seen = now - timedelta(days=10 + n)
        notice = _notice(f"{n + 1}A: Haltestelle Musterplatz {n + 1}", f"wl-notice-{n}", seen)
        notice["description"] = f"Die Haltestelle Musterplatz {n + 1} wird wegen Bauarbeiten in die Beispielgasse {n + 1} verlegt."
        notice["_identity"] = f"wl|hinweis|wl-notice-{n}"
        notices.append(notice)
        state[f"wl-notice-{n}"] = {"first_seen": seen.isoformat()}
    state_file.write_text(json.dumps(state), encoding="utf-8")
    write_cache("oebb", [serialize_for_cache(item) for item in oebb_items])
    write_cache("wl", [serialize_for_cache(item) for item in notices])

    assert bf.main() == 0

    root = ET.fromstring(out_file.read_text(encoding="utf-8"))
    titles = [str(node.text) for node in root.findall("./channel/item/title")]
    descriptions = [str(node.text) for node in root.findall("./channel/item/description")]
    return titles, descriptions, _TITLE


_TITLE = "Wien Praterstern ↔ Wien Hauptbahnhof / Wien St. Marx"


def _praterstern_like(first: date, last: date, until: str, what: str = "keine Züge fahren") -> str:
    """ÖBB's text of the closure with other days; both endpoints as ÖBB writes them, so the title stays."""
    return (
        f"{first:%d.%m.%Y} - {last:%d.%m.%Y}<br/><br/>Wegen Bauarbeiten können <br>zwischen <b>Wien Hbf </b>bzw.<b> "
        f"Wien St.Marx</b> und <b>Wien Praterstern</b><br>von <b>{first:%d.%m.%Y}</b> bis <b>{until} </b><br>{what}."
    )


def test_the_build_shows_ten_newer_notices_instead_of_the_long_closure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    titles, _, title = _build_with(monkeypatch, tmp_path)
    assert len(titles) == 10
    assert title not in titles, "the long closure past its first week still leads the notices"


def test_the_build_shows_a_short_closure_of_the_same_route_once_and_in_front(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A three-day closure of the same route closes today. It holds the one
    # slot of the title and leads the notices; the long closure goes behind
    # the field instead of standing beside it.
    now = datetime.now(UTC)
    first = (now - timedelta(days=1)).astimezone(VIENNA).date()
    last = (now + timedelta(days=1)).astimezone(VIENNA).date()
    short = _oebb(_praterstern_like(first, last, f"{last:%d.%m.%Y}", "keine Züge halten"), first, last, _TITLE)
    short["guid"] = "oebb-short"
    assert _is_oebb_closure_day(short, now)

    titles, descriptions, title = _build_with(monkeypatch, tmp_path, short)

    assert len(titles) == 10
    assert titles[0] == title
    assert "keine Züge halten" in descriptions[0]
    assert titles.count(title) == 1
