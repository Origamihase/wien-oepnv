"""A running ÖBB closure leads the notices on its closure days (decision 2026-11-08).

Live case: "Wien Hauptbahnhof ↔ Gramatneusiedl" (no local trains every
Saturday and Sunday night, 31.10. to 30.11.2026) fell to places 11 to 14
behind newer WL stop relocations on 07.11. and was not on the displays in
its own closure nights. Michael: "Auf ungeplante Störungen kann man sich
nicht vorbereiten. Auf geplante Bauarbeiten, Sperren etc schon." Unplanned
incidents keep the front; the closure stands ahead of the other notices on
the days its lead sentence names.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import src.build_feed as bf
from src.feed_build.oebb_days import _is_oebb_closure_day, _oebb_closure_days
from src.feed_types import FeedItem

GRAMATNEUSIEDL = (
    "31.10.2026 - 30.11.2026<br/><br/>Wegen Bauarbeiten können <br>zwischen <b>Wien Hbf (U)</b> und "
    "<b>Gramatneusiedl Bahnhof </b><br>von <b>31.10.2026</b> bis <b>30.11.2026 </b><span>(jeweils von "
    "Samstag 23:15 Uhr bis Sonntag 04:40 Uhr und von Sonntag 23:15 Uhr bis Montag 04:40 Uhr)<br></span>"
    "keine Nahverkehrszüge fahren.<br>Ein Schienenersatzverkehr mit Autobussen wird für Sie eingerichtet."
)
WESTBAHNHOF = (
    "11.09.2026 - 01.11.2026<br/><br/>Wegen Bauarbeiten können <br>zwischen <b>Wien Hbf (U)</b> und "
    "<b>Wien Westbahnhof (U)</b><br>von <b>23.10.2026</b> (23:00 Uhr) bis <b>27.10.2026</b> (04:00 Uhr)"
    "<br>keine Fernverkehrszüge fahren.<br>Als Ersatz haben Sie die Möglichkeit den nächsten "
    "fahrplanmäßigen Zug zu nehmen.<br>HALTAUSFALL: Von 11.09.2026 bis 09.10.2026 , am 16.10.2026 und "
    "von 23.10.2026 bis 01.11.2026 kann der Zug RJ 263 in Wien Hütteldorf Bahnhof (U) nicht halten."
)
S2 = (
    "25.10.2026 - 01.11.2026<br/><br/>Wegen Bauarbeiten kann <br>zwischen <b>Wien Leopoldau Bahnhst "
    "(U)</b> und <b>Mistelbach/Zaya Bahnhof</b><br>am <b>25</b> .10.2026 und 01 .11.2026<br>der "
    "S2-Zug 23360 nicht fahren."
)
S1 = (
    "24.10.2026 - 28.11.2026<br/><br/>Wegen Bauarbeiten kann zwischen Deutsch Wagram Bahnhof und "
    "Gänserndorf Bahnhof von <b>2</b> 4.10.2026 bis 26.10.2026 von 31.10.2026 bis 01.11.2026 der "
    "S1-Zug 23626 nicht fahren."
)
UNPLANNED = (
    "Wegen eines Schadens am Gleis sind Zugfahrten bis voraussichtlich 08.11.2026 23:59 Uhr nur "
    "eingeschränkt möglich. Planen Sie bis zu 10 Minuten mehr Reisezeit ein."
)


def test_weekday_window_keeps_the_evenings_it_begins() -> None:
    days = _oebb_closure_days(GRAMATNEUSIEDL)
    assert days is not None
    assert sorted(days) == [
        date(2026, 10, 31), date(2026, 11, 1), date(2026, 11, 7), date(2026, 11, 8),
        date(2026, 11, 14), date(2026, 11, 15), date(2026, 11, 21), date(2026, 11, 22),
        date(2026, 11, 28), date(2026, 11, 29),
    ]


def test_only_the_lead_sentence_counts_and_a_night_end_cuts_the_last_day() -> None:
    days = _oebb_closure_days(WESTBAHNHOF)
    assert days == {
        date(2026, 10, 23): None, date(2026, 10, 24): None, date(2026, 10, 25): None,
        date(2026, 10, 26): None, date(2026, 10, 27): time(4, 0),
    }


def test_single_days_and_ranges_survive_broken_digits() -> None:
    assert set(_oebb_closure_days(S2) or {}) == {date(2026, 10, 25), date(2026, 11, 1)}
    assert set(_oebb_closure_days(S1) or {}) == {
        date(2026, 10, 24), date(2026, 10, 25), date(2026, 10, 26), date(2026, 10, 31), date(2026, 11, 1),
    }


def test_a_text_without_dates_names_no_days() -> None:
    assert _oebb_closure_days(UNPLANNED) is None


def _oebb(description: str, start: datetime, end: datetime) -> FeedItem:
    return {
        "source": "ÖBB", "category": "Störung", "title": "Wien Hauptbahnhof ↔ Gramatneusiedl",
        "description": description, "guid": "oebb-gramatneusiedl", "link": "https://fahrplan.oebb.at/",
        "pubDate": datetime(2026, 9, 30, 5, 8, tzinfo=UTC), "starts_at": start, "ends_at": end,
    }


def _wl(title: str, guid: str, category: str, start: datetime) -> FeedItem:
    return {
        "source": "Wiener Linien", "category": category, "title": title, "description": f"{title}.",
        "guid": guid, "link": "https://www.wienerlinien.at/betriebsinfo", "pubDate": start,
        "starts_at": start, "ends_at": None,
    }


CLOSURE = _oebb(
    GRAMATNEUSIEDL, datetime(2026, 10, 30, 23, 0, tzinfo=UTC), datetime(2026, 11, 30, 22, 59, tzinfo=UTC)
)
SATURDAY_EVENING = datetime(2026, 11, 7, 18, 0, tzinfo=UTC)  # Sa 19:00 Vienna
WEDNESDAY = datetime(2026, 11, 4, 18, 0, tzinfo=UTC)


def _order(now: datetime) -> list[str]:
    relocation = _wl("31: Wexstraße", "wl-31", "Hinweis", datetime(2026, 11, 5, 9, 0, tzinfo=UTC))
    begun = now - timedelta(hours=1)
    incident = _wl("46: Polizeieinsatz", "wl-46", "Störung", begun)
    state: dict[str, dict[str, Any]] = {
        "oebb-gramatneusiedl": {"first_seen": "2026-10-30T23:00:00+00:00"},
        "wl-31": {"first_seen": "2026-11-05T09:00:00+00:00"},
        "wl-46": {"first_seen": begun.isoformat()},
    }
    items = [relocation, CLOSURE, incident]
    items.sort(key=lambda it: bf._recency_sort_key(it, state, now))
    return [str(it["title"]) for it in items]


def test_closure_leads_the_notices_on_its_day_behind_the_incident() -> None:
    assert _order(SATURDAY_EVENING) == ["46: Polizeieinsatz", "Wien Hauptbahnhof ↔ Gramatneusiedl", "31: Wexstraße"]


def test_closure_keeps_its_normal_place_on_other_days() -> None:
    assert _order(WEDNESDAY) == ["46: Polizeieinsatz", "31: Wexstraße", "Wien Hauptbahnhof ↔ Gramatneusiedl"]


def test_closure_day_needs_the_measure_to_run() -> None:
    assert _is_oebb_closure_day(CLOSURE, SATURDAY_EVENING)
    assert not _is_oebb_closure_day(CLOSURE, datetime(2026, 10, 25, 18, 0, tzinfo=UTC))  # before start
    westbahnhof = _oebb(
        WESTBAHNHOF, datetime(2026, 9, 10, 22, 0, tzinfo=UTC), datetime(2026, 11, 1, 22, 59, tzinfo=UTC)
    )
    assert _is_oebb_closure_day(westbahnhof, datetime(2026, 10, 27, 2, 0, tzinfo=UTC))  # 03:00 Vienna
    assert not _is_oebb_closure_day(westbahnhof, datetime(2026, 10, 27, 8, 0, tzinfo=UTC))
    assert not _is_oebb_closure_day(westbahnhof, datetime(2026, 10, 29, 8, 0, tzinfo=UTC))
    assert not _is_oebb_closure_day(_wl("26E: Gleisbauarbeiten", "wl-26e", "Störung", SATURDAY_EVENING), SATURDAY_EVENING)
