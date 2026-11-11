"""Only a real ÖBB closure leads the notices, read from the sentence the feed shows.

Independent check 2026-11-11 of the feed order of 2026-11-08
(``_is_oebb_closure_day``, decision "Hinweis mit höherer Priorität" for
ÖBB closures on their days):

* Every dated ÖBB measure ranked as a closure, also a single cancelled
  train. "S1: Wien Leopoldau ↔ Deutsch Wagram" ("der S1-Zug 23626",
  between Deutsch Wagram and Gänserndorf on 14., 15., 21., 22. and
  28.11.2026, outside Vienna) would have stood ahead of every WL notice
  on those days; from 01.10. to 11.11. single trains pushed WL notices out
  of the first ten in 159 of 1,220 replayed feed states. A dated lead now
  counts only when it says that no trains run or stop.
* The days came from ÖBB's first sentence, not from the sentence the feed
  shows (``_oebb_title_route_first``): under "Wien Hütteldorf ↔ Wien
  Hauptbahnhof" the feed showed "Teilausfall: Von 18.10.2026 bis
  19.10.2026 …", the days came from changed times between Salzburg and
  Flughafen Wien (17. to 21.10.). The shown section also writes "Von" with
  a capital letter, which the date reading missed.
* Markup inside a word ("<span> (22:00 Uhr) b</span>is 06.11.2026 (04:00
  Uhr)") hid the range of "S 40: Wien Franz-Josefs-Bahnhof ↔ Wien
  Heiligenstadt", so the closure counted on all of 06.11. instead of until
  04:00.

Mutations checked against this file (each one caught, by the test named):

* no closure check for dated leads →
  ``test_a_single_train_is_no_closure`` and
  ``test_the_single_train_keeps_its_normal_place``.
* days from ÖBB's first sentence instead of the shown one →
  ``test_the_shown_section_gives_the_days``.
* "von" only in lower case → ``test_the_shown_section_gives_the_days``.
* the old ``_plain`` reading of the raw description →
  ``test_markup_inside_a_word_keeps_the_end_time``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

import src.build_feed as bf
from src.feed_build.oebb_days import _is_oebb_closure_day, _oebb_closes_line, _oebb_closure_days
from src.feed_types import FeedItem

_VIENNA = ZoneInfo("Europe/Vienna")

# ÖBB, cache/oebb_c40d21/events.json, 11.11.2026.
S1 = (
    "24.10.2026 - 28.11.2026<br/><br/>Wegen Bauarbeiten kann zwischen <b>Deutsch Wagram Bahnhof</b> und "
    "<b>Gänserndorf Bahnhof</b> von <b>2</b> 4.10.2026 bis 26.10.2026 von 31.10.2026 bis 01.11.2026 von "
    "14.11.2026 bis 15.11.2026 von 21.11.2026 bis 22.11.2026 am 28.11.2026 und zwischen <b>Wien Leopoldau "
    "Bahnhst</b> und <b>Deutsch Wagram Bahnhof</b> am 25.10.2026 und 01.11.2026 der S1-Zug 23626 nicht "
    "fahren.<br>Ein Schienenersatzverkehr mit Autobussen wird für Sie eingerichtet."
)
GRAMATNEUSIEDL = (
    "31.10.2026 - 30.11.2026<br/><br/>Wegen Bauarbeiten können <br>zwischen <b>Wien Hbf (U)</b> und "
    "<b>Gramatneusiedl Bahnhof </b><br>von <b>31.10.2026</b> bis <b>30.11.2026 </b><span>(jeweils von "
    "Samstag 23:15 Uhr bis Sonntag 04:40 Uhr und von Sonntag 23:15 Uhr bis Montag 04:40 Uhr)<br></span>"
    "keine Nahverkehrszüge fahren.<br>Ein Schienenersatzverkehr mit Autobussen wird für Sie eingerichtet."
)
# ÖBB TRACKINFO 901477 as published 2026-10-17 (tests/test_oebb_titelstrecke_zuerst.py).
HUETTELDORF = (
    "17.10.2026 - 21.10.2026<br/><br/>Wegen Bauarbeiten fahren<br>zwischen <b>Salzburg Hbf</b> "
    "und <b>Flughafen Wien Bahnhof </b>bzw.<b> Hegyeshalom<br></b>von <b>10.10.2026</b> "
    "(22:30 Uhr) bis <b>14.10.2026</b> (04:00 Uhr) und<br>von <b>17.10.2026 </b>(22:30 Uhr) "
    "bis <b>21.10.2026 </b>(04:00 Uhr)<b><br></b>einige Fernverkehrszüge mit geänderten "
    "Fahrzeiten.<br><br><b>TEILAUSFALL:</b><br>Von <b>18.10.2026</b> bis <b>19.10.2026</b> "
    "kann der Zug RJ 263<br>zwischen <b>Wien Hütteldorf Bahnhof (U) </b>und <b>Wien Hbf (U) "
    "</b>nicht fahren.<br><br><b>HALTAUSFALL:<br></b>Von <b>11.10.2026</b> bis "
    "<b>12.10.2026</b> kann der Zug RJ 263<br>in <b>Wien Hütteldorf Bahnhof (U) </b>nicht "
    "halten.<br><br>Ihre Reisezeit verlängert sich um bis zu 8 Minuten."
)
# The same notice, its Vienna section a closure (no such text yet).
HUETTELDORF_CLOSED = HUETTELDORF.replace(
    "kann der Zug RJ 263<br>zwischen <b>Wien Hütteldorf Bahnhof (U) </b>und <b>Wien Hbf (U) </b>nicht fahren.",
    "können<br>zwischen <b>Wien Hütteldorf Bahnhof (U) </b>und <b>Wien Hbf (U) </b>keine "
    "Fernverkehrszüge fahren.",
)
# ÖBB, cache/oebb_c40d21/events.json, 31.10.2026.
S40 = (
    "31.10.2026 - 06.11.2026<br/><br/>Wegen Bauarbeiten können zwischen <b>Wien Franz-Josefs-Bahnhof</b> "
    "und <b>Wien Heiligenstadt Bahnhof (U)</b> von <b>31.10.2026</b><span> (22:00 Uhr) b</span>is "
    "<b>06.11.2026</b> (04:00 Uhr) keine R40- und S40-Züge fahren. Einige REX-Züge können zwischen "
    "<b>Wien Franz-Josefs-Bahnhof </b><span>und </span><b>Wien Heiligenstadt Bahnhof (U)</b> nicht fahren."
)


def _vienna(year: int, month: int, day: int, hour: int = 12) -> datetime:
    return datetime(year, month, day, hour, tzinfo=_VIENNA).astimezone(UTC)


def _oebb(title: str, description: str, start: date, end: date, guid: str = "oebb-1") -> FeedItem:
    return {
        "source": "ÖBB", "category": "Störung", "title": title, "description": description,
        "guid": guid, "link": "https://fahrplan.oebb.at/", "pubDate": _vienna(start.year, start.month, start.day, 0),
        "starts_at": datetime(start.year, start.month, start.day, tzinfo=_VIENNA),
        "ends_at": datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=_VIENNA),
    }


S1_ITEM = _oebb("S1: Wien Leopoldau ↔ Deutsch Wagram", S1, date(2026, 10, 24), date(2026, 11, 28), "oebb-s1")
CLOSURE_ITEM = _oebb(
    "Wien Hauptbahnhof ↔ Gramatneusiedl", GRAMATNEUSIEDL, date(2026, 10, 31), date(2026, 11, 30), "oebb-gr"
)


@pytest.mark.parametrize(
    "lead",
    [
        "Wegen Bauarbeiten können zwischen Wien Hbf (U) und Gramatneusiedl Bahnhof von 31.10.2026 bis "
        "30.11.2026 (jeweils von Samstag 23:15 Uhr bis Sonntag 04:40 Uhr und von Sonntag 23:15 Uhr bis "
        "Montag 04:40 Uhr) keine Nahverkehrszüge fahren.",
        "Wegen Bauarbeiten können zwischen St.Pölten Hbf und Wien Westbahnhof (U) von 03.11.2026 bis "
        "10.11.2026 keine REX50-Züge fahren.",
        "Wegen Bauarbeiten können von 02.11.2026 bis 03.11.2026 zwischen Wien Franz-Josefs-Bahnhof und "
        "St.Andrä-Wördern Bahnhof keine R 40-Züge fahren.",
        "Wegen Bauarbeiten können zwischen Wien Franz-Josefs-Bahnhof und Wien Heiligenstadt Bahnhof (U) von "
        "31.10.2026 (22:00 Uhr) bis 06.11.2026 (04:00 Uhr) keine R40- und S40-Züge fahren.",
        "Wegen Bauarbeiten können in Himberg b.Wien Bahnhof von 06.10.2025 bis 16.11.2026 keine Züge halten.",
        "Wegen einer Oberleitungsstörung sind zwischen Wien Heiligenstadt Bahnhof (U) und Wien "
        "Franz-Josefs-Bahnhof bis voraussichtlich 22:00 Uhr keine Fahrten möglich.",
        "Wegen Bauarbeiten ist zwischen Wien Meidling Bahnhof (U) und Wien Liesing Bahnhof am 01.12.2026 "
        "kein Zugverkehr möglich.",
    ],
)
def test_a_lead_that_stops_the_trains_closes_the_line(lead: str) -> None:
    assert _oebb_closes_line(lead)


@pytest.mark.parametrize(
    "lead",
    [
        "Wegen Bauarbeiten kann zwischen Wien Leopoldau Bahnhst (U) und Mistelbach/Zaya Bahnhof am "
        "25.10.2026 und 01.11.2026 der S2-Zug 23360 nicht fahren.",
        "Zwischen Wien Westbahnhof (U) und Wien Hütteldorf Bahnhof (U) können von 03.11.2026 bis 10.11.2026 "
        "die REX51-Züge 1604, 1608, 1614 und 1618 nicht fahren.",
        "Wegen Bauarbeiten fahren zwischen Salzburg Hbf und Flughafen Wien Bahnhof bzw. Hegyeshalom von "
        "17.10.2026 (22:30 Uhr) bis 21.10.2026 (04:00 Uhr) einige Fernverkehrszüge mit geänderten Fahrzeiten.",
        "Wegen Bauarbeiten wird von 19.03.2027 bis 30.03.2027 der Zug RJ 12265 umgeleitet.",
        "Wegen Bauarbeiten können zwischen Gänserndorf Bahnhof und Deutsch Wagram Bahnhof von 11.09.2026 "
        "(00:10 Uhr) bis 05.10.2026 (04:20 Uhr) einige Nahverkehrszüge nicht fahren.",
        "Wegen eines Schadens am Gleis sind zwischen Wien Meidling Bahnhof (U) und Wien Liesing Bahnhof "
        "Zugfahrten bis voraussichtlich 08.11.2026 23:59 Uhr nur eingeschränkt möglich.",
    ],
)
def test_single_trains_changed_times_and_diversions_close_nothing(lead: str) -> None:
    assert not _oebb_closes_line(lead)


def test_a_single_train_is_no_closure() -> None:
    # The days are read; the train is no closure (Sa 14.11., Deutsch Wagram – Gänserndorf).
    assert date(2026, 11, 14) in (_oebb_closure_days(S1) or {})
    assert not _is_oebb_closure_day(S1_ITEM, _vienna(2026, 11, 14, 10))
    assert not _is_oebb_closure_day(S1_ITEM, _vienna(2026, 10, 25, 10))


def test_the_single_train_keeps_its_normal_place() -> None:
    now = _vienna(2026, 11, 14, 10)
    notice: FeedItem = {
        "source": "Wiener Linien", "category": "Hinweis", "title": "31: Wexstraße",
        "description": "31: Wexstraße.", "guid": "wl-31", "link": "https://www.wienerlinien.at/betriebsinfo",
        "pubDate": now - timedelta(days=2), "starts_at": now - timedelta(days=2), "ends_at": None,
    }
    state: dict[str, dict[str, Any]] = {
        "oebb-s1": {"first_seen": "2026-10-23T22:00:00+00:00"},
        "oebb-gr": {"first_seen": "2026-10-30T23:00:00+00:00"},
        "wl-31": {"first_seen": (now - timedelta(days=2)).isoformat()},
    }
    items = [S1_ITEM, notice, CLOSURE_ITEM]
    items.sort(key=lambda it: bf._recency_sort_key(it, state, now))
    assert [str(it["title"]) for it in items] == [
        "Wien Hauptbahnhof ↔ Gramatneusiedl", "31: Wexstraße", "S1: Wien Leopoldau ↔ Deutsch Wagram",
    ]


def test_the_shown_section_gives_the_days() -> None:
    closed = _oebb("Wien Hütteldorf ↔ Wien Hauptbahnhof", HUETTELDORF_CLOSED, date(2026, 10, 17), date(2026, 10, 21))
    assert [day for day in range(16, 23) if _is_oebb_closure_day(closed, _vienna(2026, 10, day))] == [18, 19]
    single = _oebb("Wien Hütteldorf ↔ Wien Hauptbahnhof", HUETTELDORF, date(2026, 10, 17), date(2026, 10, 21))
    assert not any(_is_oebb_closure_day(single, _vienna(2026, 10, day)) for day in range(16, 23))


def test_markup_inside_a_word_keeps_the_end_time() -> None:
    item = _oebb("S40: Wien Franz-Josefs-Bahnhof ↔ Wien Heiligenstadt", S40, date(2026, 10, 31), date(2026, 11, 6))
    assert _is_oebb_closure_day(item, _vienna(2026, 11, 6, 3))
    assert not _is_oebb_closure_day(item, _vienna(2026, 11, 6, 5))
    assert _is_oebb_closure_day(item, _vienna(2026, 11, 5, 18))


def test_an_undated_disruption_still_counts_every_day() -> None:
    item = _oebb(
        "Wien Meidling ↔ Wien Liesing",
        "04.11.2026 - 08.11.2026<br/><br/>Wegen eines Schadens am Gleis sind zwischen <b>Wien Meidling Bahnhof "
        "(U)</b> und <b>Wien Liesing Bahnhof</b> Zugfahrten bis voraussichtlich 08.11.2026 23:59 Uhr nur "
        "eingeschränkt möglich.",
        date(2026, 11, 4), date(2026, 11, 8),
    )
    assert all(_is_oebb_closure_day(item, _vienna(2026, 11, day)) for day in range(4, 9))
