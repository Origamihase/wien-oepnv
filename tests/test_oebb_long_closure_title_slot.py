"""Who holds the one slot of an ÖBB title beside a long closure.

Operator decision 2026-11-12 ("Vorrang nur in der ersten Woche"): a closure of
more than four weeks stands among the older notices after its first week.
As the earliest window of its title it would still hold the title's one slot
(``_defer_repeated_route_titles``) until it ends, and a short closure of the
route would stand behind the field in its own closure nights. So it hands
the slot to a closure on its closure day. Everything else about the slot
is the earliest-window rule as it stood; the second half of this file pins
that part, so that a change to it is a decision and not a side effect.

Every case runs through the real ``_assemble_feed_items`` with a fixed
clock, so the wiring in ``src/build_feed.py`` is part of what is tested.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest

import src.build_feed as bf
from src.feed_build.oebb_days import _long_closure_start
from src.feed_types import FeedItem

VIENNA = ZoneInfo("Europe/Vienna")
ROUTE = "Wien Floridsdorf ↔ Wolkersdorf"
# The window ÖBB gives weekend-night works ("Wien Hauptbahnhof ↔ Gramatneusiedl", 31.10. to 30.11.2026).
NIGHTS = " <span>(jeweils von Samstag 23:15 Uhr bis Sonntag 04:40 Uhr und von Sonntag 23:15 Uhr bis Montag 04:40 Uhr)</span>"
TWO_RUNS = (
    "Wegen Bauarbeiten können zwischen Wien Floridsdorf und Wolkersdorf von 03.05.2027 bis 10.06.2027 "
    "und von 15.06.2027 bis 18.06.2027 keine Züge fahren."
)


def _at(day: date, hour: int = 13) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=VIENNA).astimezone(UTC)


def _oebb(guid: str, text: str, start: date, end: date) -> FeedItem:
    return cast(FeedItem, {
        "source": "ÖBB", "category": "Störung", "title": ROUTE, "description": text, "guid": guid,
        "_identity": f"oebb|{guid}", "link": "https://fahrplan.oebb.at/", "pubDate": _at(start - timedelta(days=2)),
        "starts_at": datetime(start.year, start.month, start.day, tzinfo=VIENNA),
        "ends_at": datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=VIENNA),
    })


def _works(first: date, last: date, what: str = "keine Züge fahren", extra: str = "") -> str:
    return (
        f"{first:%d.%m.%Y} - {last:%d.%m.%Y}<br/><br/>Wegen Bauarbeiten können <br>zwischen <b>Wien Floridsdorf</b> "
        f"und <b>Wolkersdorf</b><br>von <b>{first:%d.%m.%Y}</b> bis <b>{last:%d.%m.%Y}</b>{extra}<br>{what}."
    )


def _closure(guid: str, first: date, last: date, what: str = "keine Züge fahren", extra: str = "") -> FeedItem:
    return _oebb(guid, _works(first, last, what, extra), first, last)


def _notice(guid: str, stop: str, seen: date, line: str = "26A") -> FeedItem:
    return cast(FeedItem, {
        "source": "Wiener Linien", "category": "Hinweis", "title": f"{line}: {stop}", "guid": guid,
        "_identity": f"wl|hinweis|{guid}", "description": f"Die Haltestelle {stop} wird wegen Bauarbeiten verlegt.",
        "link": "https://www.wienerlinien.at/betriebsinfo", "pubDate": _at(seen), "starts_at": _at(seen), "ends_at": None,
    })


def _seen(**days: date) -> dict[str, dict[str, Any]]:
    return {guid.replace("_", "-"): {"first_seen": _at(day, 9).isoformat()} for guid, day in days.items()}


def _chain(items: list[FeedItem], state: dict[str, dict[str, Any]], now: datetime) -> list[str]:
    return [str(item["guid"]) for item in bf._assemble_feed_items(list(items), state, now, decide=lambda *_: None)]


def _tier(item: FeedItem, state: dict[str, dict[str, Any]], now: datetime) -> int:
    return bf._recency_sort_key(item, state, now)[0]


def _gives_way(item: FeedItem, now: datetime) -> bool:
    return _long_closure_start(item, now) is not None


def test_a_closure_on_its_day_takes_the_slot_from_the_long_closure() -> None:
    now = _at(date(2027, 5, 20))
    long_one = _closure("o-long", date(2027, 3, 1), date(2027, 8, 31))
    short = _closure("o-short", date(2027, 5, 19), date(2027, 5, 21), "keine Züge halten")
    note = _notice("w-note", "Kagraner Platz", date(2027, 5, 14))
    state = _seen(o_long=date(2027, 2, 20), o_short=date(2027, 5, 10), w_note=date(2027, 5, 14))
    assert _gives_way(long_one, now) and _tier(long_one, state, now) == 2 and _tier(short, state, now) == 1
    assert _chain([long_one, note, short], state, now) == ["o-short", "w-note", "o-long"]


@pytest.mark.parametrize("newer", ["early", "late"])
def test_of_two_closures_on_their_day_the_earlier_window_takes_the_slot(newer: str) -> None:
    now = _at(date(2027, 5, 20))
    long_one = _closure("o-long", date(2027, 3, 1), date(2027, 8, 31))
    early = _closure("o-early", date(2027, 5, 19), date(2027, 5, 21), "keine Züge halten")
    late = _closure("o-late", date(2027, 5, 20), date(2027, 5, 20), "keine Nahverkehrszüge fahren")
    first, second = (date(2027, 5, 15), date(2027, 5, 10)) if newer == "early" else (date(2027, 5, 10), date(2027, 5, 15))
    state = _seen(o_long=date(2027, 2, 20), o_early=first, o_late=second)
    assert _tier(early, state, now) == _tier(late, state, now) == 1 and _gives_way(long_one, now)
    assert _chain([long_one, late, early], state, now) == ["o-early", "o-late", "o-long"]


def test_among_long_closures_alone_the_earliest_keeps_the_slot() -> None:
    now = _at(date(2027, 5, 20))
    first = _closure("o-first", date(2027, 3, 1), date(2027, 8, 31))
    second = _closure("o-second", date(2027, 4, 1), date(2027, 6, 30), "keine Züge halten")
    note = _notice("w-note", "Kagraner Platz", date(2027, 2, 14))
    state = _seen(o_first=date(2027, 2, 20), o_second=date(2027, 3, 20), w_note=date(2027, 2, 14))
    assert _gives_way(first, now) and _gives_way(second, now)
    assert _chain([second, first, note], state, now) == ["o-first", "w-note", "o-second"]


def test_a_long_closure_in_its_first_week_keeps_the_slot() -> None:
    now = _at(date(2027, 3, 4))
    long_one = _closure("o-long", date(2027, 3, 1), date(2027, 8, 31))
    short = _closure("o-short", date(2027, 3, 3), date(2027, 3, 5), "keine Züge halten")
    state = _seen(o_long=date(2027, 2, 20), o_short=date(2027, 2, 25))
    assert not _gives_way(long_one, now) and _tier(long_one, state, now) == _tier(short, state, now) == 1
    assert _chain([short, long_one], state, now) == ["o-long", "o-short"]


def test_a_current_incident_with_a_long_period_keeps_the_slot() -> None:
    # "Störungen gehen immer vor.": ÖBB gives the disruption a period of six
    # weeks, on day 19 it is new in the feed. It would give way as a long
    # closure, and a closure of the same route has its closure day.
    now = _at(date(2027, 3, 10))
    storm = _oebb(
        "o-storm", "Wegen eines Oberleitungsschadens sind zwischen Wien Floridsdorf und Wolkersdorf derzeit keine Fahrten möglich.",
        date(2027, 2, 20), date(2027, 4, 5),
    )
    short = _closure("o-short", date(2027, 3, 9), date(2027, 3, 11))
    note = _notice("w-note", "Kagraner Platz", date(2027, 3, 5))
    state = _seen(o_short=date(2027, 3, 1), w_note=date(2027, 3, 5))
    assert _tier(storm, state, now) == 0 and _gives_way(storm, now) and _tier(short, state, now) == 1
    assert _chain([short, note, storm], state, now) == ["o-storm", "w-note", "o-short"]


def test_an_item_with_a_long_run_behind_it_and_a_closure_day_today_keeps_the_slot() -> None:
    now = _at(date(2027, 6, 16))
    both = _oebb("o-both", TWO_RUNS, date(2027, 5, 3), date(2027, 6, 18))
    other = _closure("o-other", date(2027, 6, 16), date(2027, 6, 17), "keine Züge halten")
    state = _seen(o_both=date(2027, 4, 28), o_other=date(2027, 6, 10))
    assert _gives_way(both, now) and _tier(both, state, now) == _tier(other, state, now) == 1
    assert _chain([other, both], state, now) == ["o-both", "o-other"]


# --- The earliest-window rule itself is not part of the decision ------------
# Only a long closure past its first week hands the slot on. Any other
# earliest window keeps it, also beside a later window on its closure day:
# that is the rule as it stood before 2026-11-12 (five published states on
# 09.11.2026, "Wien Hauptbahnhof ↔ Gramatneusiedl"), left as it was.


def test_weekend_nights_on_a_weekday_keep_the_slot_beside_a_closure_of_the_day() -> None:
    now = _at(date(2026, 11, 18))  # Wednesday
    weekend = _closure("o-weekend", date(2026, 11, 7), date(2026, 12, 6), "keine Nahverkehrszüge fahren", NIGHTS)
    today = _closure("o-today", date(2026, 11, 17), date(2026, 11, 19))
    note = _notice("w-note", "Kagraner Platz", date(2026, 11, 12))
    state = _seen(o_weekend=date(2026, 11, 2), o_today=date(2026, 11, 13), w_note=date(2026, 11, 12))
    assert _tier(weekend, state, now) == 2 and not _gives_way(weekend, now) and _tier(today, state, now) == 1
    assert _chain([today, note, weekend], state, now) == ["w-note", "o-weekend", "o-today"]


def test_a_long_closure_that_is_not_the_earliest_window_changes_nothing() -> None:
    now = _at(date(2026, 11, 18))  # Wednesday
    weekend = _closure("o-weekend", date(2026, 9, 5), date(2026, 12, 6), "keine Nahverkehrszüge fahren", NIGHTS)
    long_one = _closure("o-long", date(2026, 10, 5), date(2026, 12, 20), "keine Züge halten")
    today = _closure("o-today", date(2026, 11, 17), date(2026, 11, 19))
    state = _seen(o_weekend=date(2026, 9, 1), o_long=date(2026, 10, 1), o_today=date(2026, 11, 13))
    assert not _gives_way(weekend, now) and _gives_way(long_one, now) and _tier(today, state, now) == 1
    assert _chain([today, long_one, weekend], state, now) == ["o-weekend", "o-today", "o-long"]


def test_without_a_closure_day_the_long_closure_keeps_the_slot_beside_a_new_notice() -> None:
    # "Einige Züge" halt elsewhere from today: no closure, an ordinary notice.
    now = _at(date(2027, 5, 20))
    long_one = _closure("o-long", date(2027, 3, 1), date(2027, 8, 31))
    changed = _closure("o-changed", date(2027, 5, 20), date(2027, 6, 5), "einige Züge in Wolkersdorf nicht halten")
    older = [_notice(f"w-{n}", f"Musterplatz {n}", date(2027, 5, 1) + timedelta(days=n), f"{n + 1}A") for n in range(3)]
    state = _seen(o_long=date(2027, 2, 20), o_changed=date(2027, 5, 19))
    state.update({f"w-{n}": {"first_seen": _at(date(2027, 5, 1) + timedelta(days=n), 9).isoformat()} for n in range(3)})
    assert _gives_way(long_one, now) and not _gives_way(changed, now)
    assert _tier(long_one, state, now) == _tier(changed, state, now) == 2
    assert _chain([long_one, *older, changed], state, now) == ["w-2", "w-1", "w-0", "o-long", "o-changed"]


def test_on_the_eve_of_a_short_closure_the_long_closure_still_keeps_the_slot() -> None:
    friday, saturday = _at(date(2027, 5, 21)), _at(date(2027, 5, 22))
    long_one = _closure("o-long", date(2027, 3, 1), date(2027, 8, 31))
    weekend = _closure("o-weekend", date(2027, 5, 22), date(2027, 5, 23))
    note = _notice("w-note", "Kagraner Platz", date(2027, 5, 14))
    state = _seen(o_long=date(2027, 2, 20), o_weekend=date(2027, 5, 18), w_note=date(2027, 5, 14))
    assert _tier(weekend, state, friday) == 2 and not _gives_way(weekend, friday)
    assert _chain([long_one, note, weekend], state, friday) == ["w-note", "o-long", "o-weekend"]
    # On its closure day the weekend closure takes the slot and leads.
    assert _chain([long_one, note, weekend], state, saturday) == ["o-weekend", "w-note", "o-long"]
