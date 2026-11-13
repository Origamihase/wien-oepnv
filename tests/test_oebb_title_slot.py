"""Who holds the one slot of an ÖBB title (``_defer_repeated_route_titles``).

ÖBB titles are the route, so several items of one route share one slot in
front; the others stand behind the field. The slot goes to a current
incident, then to a closure on its closure day (of several the one whose
window starts first), then to the window that starts first.

Operator decision 2026-11-13, card "Soll eine Sperre an ihrem Sperrtag den
Platz ihrer Strecke im Feed bekommen?": "Ja, Sperre des Tages zuerst" ("Bei
gleichem ÖBB-Titel hält die Sperre mit Sperrtag den Platz, das frühere
Fenster rückt hinter das Feld."). Live case: "Wien Hauptbahnhof ↔
Gramatneusiedl" on 09.11.2026 from 04:31 to 06:31, where the weekend-night
window held the slot and the item of the day stood behind the field. On a
Sunday morning the window has its closure day too and keeps the slot beside
an item of the day that leads like it: operator decision 2026-11-13, card
"Soll am Sonntagmorgen die Meldung des Tages vor dem Wochenendfenster
stehen?": "Nein, Fenster behält den Platz" ("Es bleibt, wie es ist: An
Samstag und Sonntag steht das Wochenendfenster vorn, die Meldung des Tages
hinter dem Feld."). "Wie es ist" includes: an item of the day that counts
as a current incident (by the rules, or by the stored Gemini answer
"ploetzlich") holds the slot on a Sunday too. Not named by the card and
unchanged: on the first day of the window the place in the sorted order
decides.

It also settles the slot beside a closure of more than four weeks past its
first week (decision 2026-11-12, "Vorrang nur in der ersten Woche"): as the
earliest window of its title it would hold the slot until it ends, and a
short closure of the route would stand behind the field in its own closure
nights.

Every case runs through the real ``_assemble_feed_items`` with a fixed
clock, so the wiring in ``src/build_feed.py`` is part of what is tested:
the tiers behind *is_current* and *leads*, and the place of the pass behind
the sort and before the topic budget and the announced items.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest

import src.build_feed as bf
from src.feed_build.oebb_days import _long_closure_start
from src.ki_entscheidung.candidates import unclear_class
from src.feed_types import FeedItem

VIENNA = ZoneInfo("Europe/Vienna")
ROUTE = "Wien Floridsdorf ↔ Wolkersdorf"
# The window ÖBB gives weekend-night works ("Wien Hauptbahnhof ↔ Gramatneusiedl", 31.10. to 30.11.2026).
NIGHTS = " <span>(jeweils von Samstag 23:15 Uhr bis Sonntag 04:40 Uhr und von Sonntag 23:15 Uhr bis Montag 04:40 Uhr)</span>"
# ÖBB RSS, 09.11.2026: the lead sentence as it stood from 04:30 (second sentence condensed from the 04:50 text),
# and the 05:17 text cut after its second sentence.
OVERRUN = (
    "09.11.2026<br/><br/>Wegen Verlängerung der Bauarbeiten sind zwischen <b>Gramatneusiedl Bahnhof </b>und <b>Wien Hbf (U) "
    "</b>bis voraussichtlich<b> 05:30 Uhr keine Fahrten </b>möglich. Wir haben für Sie einen <b>Schienenersatzverkehr </b>"
    "eingerichtet."
)
OVERRUN_ENDED = (
    "09.11.2026<br/><br/>Wegen Verlängerung der Bauarbeiten <b>waren</b> zwischen Gramatneusiedl Bahnhof und Wien Hbf (U) "
    "<b>bis </b><b>05:15 Uhr</b> keine Fahrten möglich. Planen Sie derzeit noch bis zu<b> 10 Minuten </b>mehr Reisezeit ein."
)
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


def _chain(items: list[FeedItem], state: dict[str, dict[str, Any]], now: datetime, sudden: tuple[str, ...] = ()) -> list[str]:
    def decide(feed_items: list[FeedItem], _state: dict[str, Any], _now: datetime) -> None:
        # Stands for the Gemini step: the stored answer "ploetzlich" for the items named in *sudden*.
        for item in feed_items:
            if item.get("guid") in sudden:
                item["_ki_art"] = "ploetzlich"

    return [str(item["guid"]) for item in bf._assemble_feed_items(list(items), state, now, decide=decide)]


def _tier(item: FeedItem, state: dict[str, dict[str, Any]], now: datetime) -> int:
    return bf._recency_sort_key(item, state, now)[0]


def _long_past_first_week(item: FeedItem, now: datetime) -> bool:
    return _long_closure_start(item, now) is not None


def test_a_closure_on_its_day_takes_the_slot_from_the_long_closure() -> None:
    now = _at(date(2027, 5, 20))
    long_one = _closure("o-long", date(2027, 3, 1), date(2027, 8, 31))
    short = _closure("o-short", date(2027, 5, 19), date(2027, 5, 21), "keine Züge halten")
    note = _notice("w-note", "Kagraner Platz", date(2027, 5, 14))
    old = _notice("w-old", "Praterstern", date(2027, 2, 1), "5B")  # older than the long closure: behind it without the pass
    state = _seen(o_long=date(2027, 2, 20), o_short=date(2027, 5, 10), w_note=date(2027, 5, 14), w_old=date(2027, 2, 1))
    assert _long_past_first_week(long_one, now) and _tier(long_one, state, now) == 2 and _tier(short, state, now) == 1
    assert _chain([long_one, note, old, short], state, now) == ["o-short", "w-note", "w-old", "o-long"]


@pytest.mark.parametrize(("newer", "older"), [("early", "late"), ("late", "early")])
def test_of_two_closures_on_their_day_the_earlier_window_takes_the_slot(newer: str, older: str) -> None:
    # Both lead. The window decides, not which of the two the feed saw later.
    now = _at(date(2027, 5, 20))
    long_one = _closure("o-long", date(2027, 3, 1), date(2027, 8, 31))
    early = _closure("o-early", date(2027, 5, 19), date(2027, 5, 21), "keine Züge halten")
    late = _closure("o-late", date(2027, 5, 20), date(2027, 5, 20), "keine Nahverkehrszüge fahren")
    note = _notice("w-note", "Kagraner Platz", date(2027, 5, 14))
    state = _seen(o_long=date(2027, 2, 20), w_note=date(2027, 5, 14), **{f"o_{newer}": date(2027, 5, 15), f"o_{older}": date(2027, 5, 10)})
    assert _tier(early, state, now) == _tier(late, state, now) == 1 and _long_past_first_week(long_one, now)
    assert _chain([long_one, late, note, early], state, now) == ["o-early", "w-note", "o-late", "o-long"]


def test_among_long_closures_alone_the_earliest_keeps_the_slot() -> None:
    now = _at(date(2027, 5, 20))
    first = _closure("o-first", date(2027, 3, 1), date(2027, 8, 31))
    second = _closure("o-second", date(2027, 4, 1), date(2027, 6, 30), "keine Züge halten")
    note = _notice("w-note", "Kagraner Platz", date(2027, 2, 14))
    state = _seen(o_first=date(2027, 2, 20), o_second=date(2027, 3, 20), w_note=date(2027, 2, 14))
    assert _long_past_first_week(first, now) and _long_past_first_week(second, now)
    assert _chain([second, first, note], state, now) == ["o-first", "w-note", "o-second"]


@pytest.mark.parametrize(("newer", "older"), [("long", "short"), ("short", "long")])
def test_a_long_closure_in_its_first_week_keeps_the_slot(newer: str, older: str) -> None:
    # In its first week it leads like any closure on its day, and its window starts first.
    now = _at(date(2027, 3, 4))
    long_one = _closure("o-long", date(2027, 3, 1), date(2027, 8, 31))
    short = _closure("o-short", date(2027, 3, 3), date(2027, 3, 5), "keine Züge halten")
    note = _notice("w-note", "Kagraner Platz", date(2027, 2, 28))
    state = _seen(w_note=date(2027, 2, 28), **{f"o_{newer}": date(2027, 2, 25), f"o_{older}": date(2027, 2, 20)})
    assert not _long_past_first_week(long_one, now) and _tier(long_one, state, now) == _tier(short, state, now) == 1
    assert _chain([short, note, long_one], state, now) == ["o-long", "w-note", "o-short"]


def test_a_current_incident_with_a_long_period_keeps_the_slot() -> None:
    # "Störungen gehen immer vor.": ÖBB gives the disruption a period of six
    # weeks, on day 19 it is new in the feed. A closure of the same route has
    # its closure day and would take the slot from anything but a current
    # incident.
    now = _at(date(2027, 3, 10))
    storm = _oebb(
        "o-storm", "Wegen eines Oberleitungsschadens sind zwischen Wien Floridsdorf und Wolkersdorf derzeit keine Fahrten möglich.",
        date(2027, 2, 20), date(2027, 4, 5),
    )
    short = _closure("o-short", date(2027, 3, 9), date(2027, 3, 11))
    note = _notice("w-note", "Kagraner Platz", date(2027, 3, 5))
    state = _seen(o_short=date(2027, 3, 1), w_note=date(2027, 3, 5))
    assert _tier(storm, state, now) == 0 and _tier(short, state, now) == 1
    assert _chain([short, note, storm], state, now) == ["o-storm", "w-note", "o-short"]


def test_of_two_current_incidents_the_one_in_front_keeps_the_slot() -> None:
    # The older incident has the earlier window; the newer one stands in
    # front, and the older one goes behind the notice.
    now = _at(date(2027, 3, 10))
    text = "Wegen eines Oberleitungsschadens sind zwischen Wien Floridsdorf und Wolkersdorf derzeit keine Fahrten möglich."
    older = _oebb("o-older", text, date(2027, 3, 9), date(2027, 3, 10))
    newer = _oebb("o-newer", text.replace("eines Oberleitungsschadens", "einer Weichenstörung"), date(2027, 3, 10), date(2027, 3, 11))
    note = _notice("w-note", "Kagraner Platz", date(2027, 3, 5))
    state = _seen(w_note=date(2027, 3, 5))
    state["o-older"] = {"first_seen": _at(date(2027, 3, 9), 20).isoformat()}
    state["o-newer"] = {"first_seen": _at(date(2027, 3, 10), 12).isoformat()}
    assert _tier(older, state, now) == _tier(newer, state, now) == 0
    assert _chain([older, note, newer], state, now) == ["o-newer", "w-note", "o-older"]


def test_an_item_with_a_long_run_behind_it_and_a_closure_day_today_takes_the_slot() -> None:
    # Its first run (03.05. to 10.06.) is long and over; the second closes
    # today, so the item leads and takes the slot from the earliest window.
    now = _at(date(2027, 6, 16))
    both = _oebb("o-both", TWO_RUNS, date(2027, 5, 3), date(2027, 6, 18))
    long_one = _closure("o-long", date(2027, 3, 1), date(2027, 8, 31), "keine Züge halten")
    note = _notice("w-note", "Kagraner Platz", date(2027, 6, 10))
    old = _notice("w-old", "Praterstern", date(2027, 2, 1), "5B")  # older than the window that goes back: behind it without the pass
    state = _seen(o_both=date(2027, 4, 28), o_long=date(2027, 2, 20), w_note=date(2027, 6, 10), w_old=date(2027, 2, 1))
    assert _long_past_first_week(both, now) and (_tier(both, state, now), _tier(long_one, state, now)) == (1, 2)
    assert _chain([long_one, note, old, both], state, now) == ["o-both", "w-note", "w-old", "o-long"]


# --- A closure on its closure day before an earlier window (13.11.2026) ------


@pytest.mark.parametrize(("text", "hour"), [(OVERRUN, 5), (OVERRUN_ENDED, 6)])
def test_gramatneusiedl_on_a_monday_morning_shows_the_item_of_the_day(text: str, hour: int) -> None:
    # 09.11.2026: the weekend nights are over at 04:40, the works overran.
    # The window of the weekend nights starts first and held the slot (place
    # 11 to 15 in the replay); the item of the day stood on place 61 to 64.
    now = _at(date(2026, 11, 9), hour)
    nights = _closure("o-nights", date(2026, 10, 31), date(2026, 11, 30), "keine Nahverkehrszüge fahren", NIGHTS)
    december = _closure("o-december", date(2026, 12, 5), date(2026, 12, 7), "keine Nahverkehrszüge fahren")
    today = _oebb("o-today", text, date(2026, 11, 9), date(2026, 11, 9))
    note = _notice("w-note", "Kagraner Platz", date(2026, 11, 6))
    old = _notice("w-old", "Praterstern", date(2026, 10, 15), "5B")  # older than the window: behind it without the pass
    state = _seen(o_nights=date(2026, 10, 20), o_december=date(2026, 11, 3), w_note=date(2026, 11, 6), w_old=date(2026, 10, 15))
    state["o-today"] = {"first_seen": _at(date(2026, 11, 9), 4).isoformat()}
    assert (_tier(nights, state, now), _tier(today, state, now)) == (2, 1)
    assert _chain([nights, december, note, old, today], state, now) == ["o-today", "w-note", "w-old", "o-nights", "o-december"]


@pytest.mark.parametrize(("text", "hour"), [(OVERRUN, 5), (OVERRUN_ENDED, 6)])
def test_on_a_sunday_morning_the_weekend_window_keeps_the_slot(text: str, hour: int) -> None:
    # The same overrun one day earlier. On Sunday the window of the weekend
    # nights has its closure day as well, both lead, and the window starts
    # first: the item of the day stands behind the field, as before the
    # decision of 2026-11-13. The operator kept it that way on the same day
    # ("Nein, Fenster behält den Platz"). No Gemini answer here; with the
    # answer "ploetzlich" the first text is a current incident (further
    # below). The place in the sorted order would put the item of the day in
    # front here, and a report that closes nothing before the window in its
    # closure night (next test).
    now = _at(date(2026, 11, 8), hour)
    nights = _closure("o-nights", date(2026, 10, 31), date(2026, 11, 30), "keine Nahverkehrszüge fahren", NIGHTS)
    december = _closure("o-december", date(2026, 12, 5), date(2026, 12, 7), "keine Nahverkehrszüge fahren")
    today = _oebb("o-today", text.replace("09.11.2026", "08.11.2026"), date(2026, 11, 8), date(2026, 11, 8))
    note = _notice("w-note", "Kagraner Platz", date(2026, 11, 6))
    state = _seen(o_nights=date(2026, 10, 20), o_december=date(2026, 11, 3), w_note=date(2026, 11, 6))
    state["o-today"] = {"first_seen": _at(date(2026, 11, 8), 4).isoformat()}
    assert (_tier(nights, state, now), _tier(today, state, now)) == (1, 1)
    assert _chain([nights, december, note, today], state, now) == ["o-nights", "w-note", "o-today", "o-december"]


def test_in_its_closure_night_the_window_keeps_the_slot_beside_a_newer_report_that_closes_nothing() -> None:
    # ÖBB wording of 04.11.2026 ("Wien Meidling ↔ Wien Liesing"). Past its 24
    # hours as a current incident the report leads like the window; it is the
    # newer one, but the window starts first and closes the line tonight.
    now = _at(date(2026, 11, 7), 22)  # Saturday
    nights = _closure("o-nights", date(2026, 10, 31), date(2026, 11, 30), "keine Nahverkehrszüge fahren", NIGHTS)
    text = (
        "Wegen eines Schadens am Gleis sind Zugfahrten bis voraussichtlich 08.11.2026 23:59 Uhr nur eingeschränkt möglich. "
        "Planen Sie bis zu 10 Minuten mehr Reisezeit ein."
    )
    report = _oebb("o-report", text, date(2026, 11, 4), date(2026, 11, 8))
    note = _notice("w-note", "Kagraner Platz", date(2026, 11, 6))
    state = _seen(o_nights=date(2026, 10, 20), w_note=date(2026, 11, 6))
    state["o-report"] = {"first_seen": _at(date(2026, 11, 4), 19).isoformat()}
    assert (_tier(nights, state, now), _tier(report, state, now)) == (1, 1)
    assert _chain([report, note, nights], state, now) == ["o-nights", "w-note", "o-report"]


def test_on_a_sunday_morning_a_current_incident_still_takes_the_slot_from_the_weekend_window() -> None:
    # The decision "Nein, Fenster behält den Platz" is about two members
    # that lead. A current incident of the same route goes first on a Sunday
    # as on any day (operator decision 2026-10-04).
    now = _at(date(2026, 11, 8), 5)
    nights = _closure("o-nights", date(2026, 10, 31), date(2026, 11, 30), "keine Nahverkehrszüge fahren", NIGHTS)
    storm = _oebb(
        "o-storm", "Wegen eines Oberleitungsschadens sind zwischen Wien Floridsdorf und Wolkersdorf derzeit keine Fahrten möglich.",
        date(2026, 11, 8), date(2026, 11, 8),
    )
    note = _notice("w-note", "Kagraner Platz", date(2026, 11, 6))
    state = _seen(o_nights=date(2026, 10, 20), w_note=date(2026, 11, 6))
    assert (_tier(storm, state, now), _tier(nights, state, now)) == (0, 1)
    assert _chain([nights, note, storm], state, now) == ["o-storm", "w-note", "o-nights"]


@pytest.mark.parametrize(
    ("text", "hour", "expected"),
    [(OVERRUN, 5, ["o-today", "w-note", "o-nights"]), (OVERRUN_ENDED, 6, ["o-nights", "w-note", "o-today"])],
)
def test_on_a_sunday_morning_the_overrun_holds_the_slot_while_gemini_calls_it_sudden(text: str, hour: int, expected: list[str]) -> None:
    # "Es bleibt, wie es ist" includes the Gemini step (since 2026-11-12):
    # for the overrun of 09.11. the case collection expects "ploetzlich"
    # (data/samples/ki_entscheidung_faelle.json), and the probe runs of
    # 12.11.2026 gave that answer (docs/architecture-teil-16.md, section 14,
    # "Probelauf"). While the text closes the line the item of the day is
    # then a current incident and holds the slot on a Sunday too; once it
    # reads "waren bis 05:15 Uhr" the step no longer asks about it, the
    # answer no longer counts and the window has the slot back.
    now = _at(date(2026, 11, 8), hour)
    nights = _closure("o-nights", date(2026, 10, 31), date(2026, 11, 30), "keine Nahverkehrszüge fahren", NIGHTS)
    today = _oebb("o-today", text.replace("09.11.2026", "08.11.2026"), date(2026, 11, 8), date(2026, 11, 8))
    today["pubDate"] = _at(date(2026, 11, 8), 4)  # ÖBB published the overrun on its day (09.11.: 04:30)
    note = _notice("w-note", "Kagraner Platz", date(2026, 11, 6))
    state = _seen(o_nights=date(2026, 10, 20), w_note=date(2026, 11, 6))
    state["o-today"] = {"first_seen": _at(date(2026, 11, 8), 4).isoformat()}
    assert unclear_class(today, state["o-today"], now) == ("oebb-tag" if text is OVERRUN else None)  # what the step asks about
    assert _chain([nights, note, today], state, now, sudden=("o-today",)) == expected


@pytest.mark.parametrize(("text", "hour", "minute"), [(OVERRUN, 5, 0), (OVERRUN_ENDED, 23, 31)])
def test_on_the_first_day_of_the_weekend_window_the_sorted_order_decides(text: str, hour: int, minute: int) -> None:
    # Saturday 31.10.2026: window and item of the day start on the same day,
    # both lead, and as between any two windows of one day the place in the
    # sorted order decides (tests/test_repeated_route_titles.py). The item of
    # the day is the newer one and stands in front, at 23:31 also with a text
    # that closes nothing any more, in the first closure night of the window
    # (from 23:15). Not named by the card of 2026-11-13; pinned as it is.
    now = _at(date(2026, 10, 31), hour) + timedelta(minutes=minute)
    nights = _closure("o-nights", date(2026, 10, 31), date(2026, 11, 30), "keine Nahverkehrszüge fahren", NIGHTS)
    today = _oebb("o-today", text.replace("09.11.2026", "31.10.2026"), date(2026, 10, 31), date(2026, 10, 31))
    today["pubDate"] = _at(date(2026, 10, 31), 4)
    note = _notice("w-note", "Kagraner Platz", date(2026, 10, 29))
    state = _seen(o_nights=date(2026, 10, 20), w_note=date(2026, 10, 29))
    state["o-today"] = {"first_seen": _at(date(2026, 10, 31), 4).isoformat()}
    assert (_tier(nights, state, now), _tier(today, state, now)) == (1, 1)
    assert _chain([nights, note, today], state, now) == ["o-today", "w-note", "o-nights"]


def test_the_pass_stands_before_the_topic_budget_and_the_announced_items() -> None:
    # Behind the field the order is: repeated route titles, what the topic
    # budget moved back, what is only announced.
    now = _at(date(2026, 11, 18))  # Wednesday
    weekend = _closure("o-weekend", date(2026, 11, 7), date(2026, 12, 6), "keine Nahverkehrszüge fahren", NIGHTS)
    today = _closure("o-today", date(2026, 11, 17), date(2026, 11, 19))
    note = _notice("w-note", "Kagraner Platz", date(2026, 11, 12))
    announced = _notice("w-announced", "Praterstern", date(2026, 11, 16), "5B")
    announced["starts_at"] = _at(date(2026, 12, 14))
    state = _seen(o_weekend=date(2026, 11, 2), o_today=date(2026, 11, 13), w_note=date(2026, 11, 12), w_announced=date(2026, 11, 16))
    old = _notice("w-old", "Stephansplatz", date(2026, 10, 20), "7B")  # older than the window that goes back
    state["w-old"] = {"first_seen": _at(date(2026, 10, 20), 9).isoformat()}
    demos = []
    for n in range(bf.feed_config.MAX_ITEMS_PER_TOPIC + 1):
        demo = _notice(f"w-demo{n}", "x", date(2026, 11, 18), str(n + 1))
        demo["title"], demo["category"] = f"{n + 1}: Demonstration Betrieb ab 14:00", "Störung"
        demo["starts_at"] = _at(date(2026, 11, 18), 9 + n)
        demo["description"] = f"Linie {n + 1}: Wegen einer Demonstration unregelmäßige Intervalle bei der Station Nummer {n}."
        demos.append(demo)
        state[f"w-demo{n}"] = {"first_seen": _at(date(2026, 11, 17), 1 + n).isoformat()}
    kept = [f"w-demo{n}" for n in range(bf.feed_config.MAX_ITEMS_PER_TOPIC)]
    over = f"w-demo{bf.feed_config.MAX_ITEMS_PER_TOPIC}"
    expected = ["o-today", *kept, "w-note", "w-old", "o-weekend", over, "w-announced"]
    assert _chain([today, note, weekend, old, announced, *demos], state, now) == expected


def test_a_closure_of_the_day_takes_the_slot_from_weekend_nights_on_a_weekday() -> None:
    now = _at(date(2026, 11, 18))  # Wednesday
    weekend = _closure("o-weekend", date(2026, 11, 7), date(2026, 12, 6), "keine Nahverkehrszüge fahren", NIGHTS)
    today = _closure("o-today", date(2026, 11, 17), date(2026, 11, 19))
    note = _notice("w-note", "Kagraner Platz", date(2026, 11, 12))
    old = _notice("w-old", "Praterstern", date(2026, 10, 20), "5B")  # older than the window that goes back: behind it without the pass
    state = _seen(o_weekend=date(2026, 11, 2), o_today=date(2026, 11, 13), w_note=date(2026, 11, 12), w_old=date(2026, 10, 20))
    assert _tier(weekend, state, now) == 2 and _tier(today, state, now) == 1
    assert _chain([today, note, old, weekend], state, now) == ["o-today", "w-note", "w-old", "o-weekend"]
    # The day after its last day the closure has left the feed and the window holds the slot again.
    after = _at(date(2026, 11, 20))
    assert _chain([today, note, old, weekend], state, after) == ["w-note", "o-weekend", "w-old"]


def test_a_closure_of_the_day_takes_the_slot_whatever_stands_before_it() -> None:
    now = _at(date(2026, 11, 18))  # Wednesday
    weekend = _closure("o-weekend", date(2026, 9, 5), date(2026, 12, 6), "keine Nahverkehrszüge fahren", NIGHTS)
    long_one = _closure("o-long", date(2026, 10, 5), date(2026, 12, 20), "keine Züge halten")
    today = _closure("o-today", date(2026, 11, 17), date(2026, 11, 19))
    old = _notice("w-old", "Praterstern", date(2026, 8, 20), "5B")  # older than both windows: behind them without the pass
    state = _seen(o_weekend=date(2026, 9, 1), o_long=date(2026, 10, 1), o_today=date(2026, 11, 13), w_old=date(2026, 8, 20))
    assert _long_past_first_week(long_one, now) and _tier(today, state, now) == 1
    assert (_tier(weekend, state, now), _tier(long_one, state, now)) == (2, 2)
    assert _chain([today, long_one, old, weekend], state, now) == ["o-today", "w-old", "o-long", "o-weekend"]


def test_a_report_of_the_day_without_a_closure_leads_like_one_and_takes_the_slot() -> None:
    # What leads is tier 1 of the sort key, and an ÖBB text that names no day
    # counts on every day of its period whatever it reports (oebb_days). Past
    # its 24 hours as a current incident such a report stands before the
    # notices, and so it holds the slot of its title as well.
    now = _at(date(2026, 11, 18), 21)  # Wednesday
    weekend = _closure("o-weekend", date(2026, 11, 7), date(2026, 12, 6), "keine Nahverkehrszüge fahren", NIGHTS)
    text = "Wegen einer Oberleitungsstörung sind zwischen Wien Floridsdorf und Wolkersdorf Zugfahrten derzeit nur eingeschränkt möglich."
    report = _oebb("o-report", text, date(2026, 11, 17), date(2026, 11, 18))
    note = _notice("w-note", "Kagraner Platz", date(2026, 11, 12))
    old = _notice("w-old", "Praterstern", date(2026, 10, 20), "5B")  # older than the window that goes back: behind it without the pass
    state = _seen(o_weekend=date(2026, 11, 2), w_note=date(2026, 11, 12), w_old=date(2026, 10, 20))
    state["o-report"] = {"first_seen": _at(date(2026, 11, 17), 15).isoformat()}
    assert (_tier(weekend, state, now), _tier(report, state, now)) == (2, 1)
    assert _chain([weekend, note, old, report], state, now) == ["o-report", "w-note", "w-old", "o-weekend"]


def test_a_long_closure_past_its_first_week_takes_no_slot_from_an_earlier_window() -> None:
    # Decision 2026-11-12: past its first week it is a notice like the others,
    # also for the slot. The window of the weekend nights starts first.
    now = _at(date(2026, 11, 18))  # Wednesday
    weekend = _closure("o-weekend", date(2026, 9, 5), date(2026, 12, 6), "keine Nahverkehrszüge fahren", NIGHTS)
    long_one = _closure("o-long", date(2026, 10, 5), date(2026, 12, 20), "keine Züge halten")
    note = _notice("w-note", "Kagraner Platz", date(2026, 11, 12))
    state = _seen(o_weekend=date(2026, 9, 1), o_long=date(2026, 10, 1), w_note=date(2026, 11, 12))
    assert _long_past_first_week(long_one, now) and (_tier(weekend, state, now), _tier(long_one, state, now)) == (2, 2)
    assert _chain([long_one, note, weekend], state, now) == ["w-note", "o-weekend", "o-long"]


# --- Without a closure on its closure day the window that starts first holds the slot ---


def test_without_a_closure_day_the_long_closure_keeps_the_slot_beside_a_new_notice() -> None:
    # "Einige Züge" halt elsewhere from today: no closure, an ordinary notice.
    now = _at(date(2027, 5, 20))
    long_one = _closure("o-long", date(2027, 3, 1), date(2027, 8, 31))
    changed = _closure("o-changed", date(2027, 5, 20), date(2027, 6, 5), "einige Züge in Wolkersdorf nicht halten")
    older = [_notice(f"w-{n}", f"Musterplatz {n}", date(2027, 5, 1) + timedelta(days=n), f"{n + 1}A") for n in range(3)]
    state = _seen(o_long=date(2027, 2, 20), o_changed=date(2027, 5, 19))
    state.update({f"w-{n}": {"first_seen": _at(date(2027, 5, 1) + timedelta(days=n), 9).isoformat()} for n in range(3)})
    assert _long_past_first_week(long_one, now) and not _long_past_first_week(changed, now)
    assert _tier(long_one, state, now) == _tier(changed, state, now) == 2
    assert _chain([long_one, *older, changed], state, now) == ["w-2", "w-1", "w-0", "o-long", "o-changed"]


def test_on_the_eve_of_a_short_closure_the_long_closure_still_keeps_the_slot() -> None:
    friday, saturday = _at(date(2027, 5, 21)), _at(date(2027, 5, 22))
    long_one = _closure("o-long", date(2027, 3, 1), date(2027, 8, 31))
    weekend = _closure("o-weekend", date(2027, 5, 22), date(2027, 5, 23))
    note = _notice("w-note", "Kagraner Platz", date(2027, 5, 14))
    state = _seen(o_long=date(2027, 2, 20), o_weekend=date(2027, 5, 18), w_note=date(2027, 5, 14))
    assert _tier(weekend, state, friday) == 2 and not _long_past_first_week(weekend, friday)
    assert _chain([long_one, note, weekend], state, friday) == ["w-note", "o-long", "o-weekend"]
    # On its closure day the weekend closure takes the slot and leads.
    assert _chain([long_one, note, weekend], state, saturday) == ["o-weekend", "w-note", "o-long"]


def test_two_windows_without_a_closure_today_leave_the_slot_with_the_earlier_one() -> None:
    now = _at(date(2026, 11, 18))  # Wednesday: neither weekend window closes
    first = _closure("o-first", date(2026, 11, 7), date(2026, 12, 6), "keine Nahverkehrszüge fahren", NIGHTS)
    second = _closure("o-second", date(2026, 11, 14), date(2026, 12, 13), "keine Züge halten", NIGHTS)
    state = _seen(o_first=date(2026, 11, 1), o_second=date(2026, 11, 10))
    assert _tier(first, state, now) == _tier(second, state, now) == 2
    assert _chain([second, first], state, now) == ["o-first", "o-second"]
