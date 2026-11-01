"""A WL notice gives way while WL's live incident reports the same measures.

On 2026-10-26 (Nationalfeiertag) the notice "1/2/71/D: Nationalfeiertag"
(<h2> "Veranstaltung", Ringstraße) and the live incident "1/2/71/D:
Veranstaltung" (I20261026-0004, no end) stood side by side from 09:01 to
17:31 Vienna time. Found by the KI watcher on 2026-11-01. ``_absorb_works_tickers`` never took the incident: an
incident without an end never lies within the notice. Now the notice gives
way while the incident runs and covers all of its lines
(``_yield_notices_to_incidents``). An incident on only some of the notice's
lines (22.10., "4A/74A: Demonstration" beside the notice of 16 lines) says
nothing about the others, and both stay. Only a notice of one operating
day gives way: a notice of weeks ("1: Gleisbauarbeiten", 21.10. to
05.12.2026) tells what holds after a short incident of its cause, and stays.

The shapes are the entries of ``cache/wl_9d709a/events.json`` of those days.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import src.build_feed as bf
from src.feed_types import FeedItem

CET = timezone(timedelta(hours=1))
NOW = datetime(2026, 10, 26, 16, 1, tzinfo=CET)

NOTICE_HTML = (
    "<h2>Veranstaltung</h2><p>Wegen einer Veranstaltung im Bereich der Ringstraße kommt es zu folgenden "
    "Verkehrsmaßnahmen.</p><p><strong><u>Zeitraum:</u></strong><br>Montag, 26. Oktober 2026, ab etwa 06:30 "
    "Uhr bis etwa 18:00 Uhr.</p><p><strong><u>Maßnahmen:</u></strong></p><p><u>Linie D</u><br>Umleitung in "
    "beiden Richtungen<br>zwischen Schwarzenbergplatz und Börse<br>über Schwedenplatz U.</p><p><u>Linie 1</u>"
    "<br>Umleitung in beiden Richtungen<br>zwischen Karlsplatz U und Julius-Raab-Platz<br>über Schubertring "
    "und Stubenring.</p><p><u>Linie 2</u><br>Kein Betrieb zwischen Parlament, U Volkstheater und "
    "Schwedenplatz U.</p><p><u>Linie 71 (ganztägig)</u><br>Betrieb nur zwischen Kaiserebersdorf und "
    "Schwarzenbergplatz.</p>"
)
INCIDENT_TEXT = (
    "Linie 1: Umleitung in beiden Richtungen zwischen Julius-Raab-Platz und Karlsplatz U über Stubentor U "
    "und Schwarzenbergplatz. Linie 2: Kein Betrieb zwischen Parlament, U Volkstheater und Schwedenplatz U. "
    "Linie D: Umleitung in beiden Richtungen zwischen Schwarzenbergplatz und Börse über Stubentor U und "
    "Schwedenplatz U. Linie 71: Betrieb nur zwischen Kaiserebersdorf und Schwarzenbergplatz. Ersatz: Linien "
    "U1, U2, U3 und U4. Voraussichtliche Dauer: 18:00 Uhr. Grund: Veranstaltung im Bereich Ringstraße."
)


def _notice(title: str = "1/2/71/D: Nationalfeiertag", description: str = NOTICE_HTML, **extra: Any) -> FeedItem:
    item: dict[str, Any] = {
        "source": "Wiener Linien",
        "category": "Hinweis",
        "title": title,
        "description": description,
        "link": "https://www.wienerlinien.at/betriebsinfo",
        "guid": "5ed1a558f4654252adcbde0d5b0507c31199017b815d336b01f7b063bfcb22b7",
        "pubDate": datetime(2026, 10, 16, tzinfo=timezone(timedelta(hours=2))),
        "starts_at": datetime(2026, 10, 26, tzinfo=CET),
        "ends_at": datetime(2026, 10, 26, 23, 59, tzinfo=CET),
        "_identity": "wl|hinweis|L=1,2,71,D|D=2026-10-26|TK=d 1 2 71 nationalfeiertag",
    }
    item.update(extra)
    return item  # type: ignore[return-value]


def _incident(
    title: str = "1/2/71/D: Veranstaltung",
    description: str = INCIDENT_TEXT,
    start: datetime = datetime(2026, 10, 26, 6, 27, tzinfo=CET),
    *,
    with_incident: bool = True,
) -> FeedItem:
    item: dict[str, Any] = {
        "source": "Wiener Linien",
        "category": "Störung",
        "title": title,
        "description": description,
        "link": "https://www.wienerlinien.at/betriebsinfo",
        "guid": "3d9dc833154fb13a2a35439b5dec0828f5c1624ba8b8558e713d29e16154f7dd",
        "pubDate": start,
        "starts_at": start,
        "ends_at": None,
        "_identity": "wl|störung|L=1,2,71,D|D=2026-10-26|TK=veranstaltung",
    }
    if with_incident:
        item["_wl_ids"] = ["I20261026-0004"]
        item["_incident_start"] = start
    return item  # type: ignore[return-value]


def _titles(items: list[FeedItem]) -> list[str]:
    return [str(item["title"]) for item in items]


def test_the_notice_gives_way_to_the_running_incident() -> None:
    items = [_notice(), _incident()]
    assert _titles(bf._yield_notices_to_incidents(items, NOW)) == ["1/2/71/D: Veranstaltung"]


def test_the_notice_is_back_when_the_incident_has_ended() -> None:
    assert _titles(bf._yield_notices_to_incidents([_notice()], NOW)) == ["1/2/71/D: Nationalfeiertag"]


def test_an_incident_on_some_of_the_notice_lines_leaves_the_notice() -> None:
    # 22.10.2026: "4A/74A: Demonstration" beside the notice of 16 lines.
    incident = _incident(
        "1/2/D/71: Demonstration",
        "Linie 71: Betrieb nur zwischen Kaiserebersdorf und Schwarzenbergplatz. Voraussichtliche Dauer: "
        "18:00 Uhr. Grund: Veranstaltung im Bereich Ringstraße.",
    )
    incident["title"] = "71: Veranstaltung Betrieb ab Schwarzenbergplatz"
    items = [_notice(), incident]
    assert len(bf._yield_notices_to_incidents(items, NOW)) == 2


def test_an_incident_of_another_cause_leaves_the_notice() -> None:
    incident = _incident(
        "1/2/71/D: Verkehrsunfall",
        INCIDENT_TEXT.replace("Grund: Veranstaltung im Bereich Ringstraße.", "Grund: Verkehrsunfall im Bereich Ringstraße."),
    )
    assert len(bf._yield_notices_to_incidents([_notice(), incident], NOW)) == 2


def test_an_incident_at_another_place_leaves_the_notice() -> None:
    incident = _incident(
        description="Linie D: Umleitung. Voraussichtliche Dauer: 18:00 Uhr. Grund: Veranstaltung im Bereich Prater Hauptallee.",
    )
    assert len(bf._yield_notices_to_incidents([_notice(), incident], NOW)) == 2


def test_display_tickers_without_an_incident_leave_the_notice() -> None:
    # Tickers of the board are fragments; they go up in the notice instead
    # (``_absorb_works_tickers``), never the other way round.
    ticker = _incident(description="Veranstaltung im Bereich Ringstraße", with_incident=False)
    assert len(bf._yield_notices_to_incidents([_notice(), ticker], NOW)) == 2


def test_a_notice_not_yet_in_force_stays() -> None:
    # 25.10.2026 evening: the notice of 26.10. beside the incident of 25.10.
    evening = datetime(2026, 10, 25, 20, 0, tzinfo=CET)
    incident = _incident(start=datetime(2026, 10, 25, 6, 0, tzinfo=CET))
    assert len(bf._yield_notices_to_incidents([_notice(), incident], evening)) == 2


def test_an_incident_older_than_the_notice_leaves_it() -> None:
    # An incident that began more than a day before the notice is another one.
    incident = _incident(start=datetime(2026, 10, 24, 6, 0, tzinfo=CET))
    assert len(bf._yield_notices_to_incidents([_notice(), incident], NOW)) == 2


def test_a_notice_of_weeks_stays_beside_a_short_incident() -> None:
    # "1: Gleisbauarbeiten" ran from 21.10. to 05.12.2026 04:00.
    notice = _notice(ends_at=datetime(2026, 12, 5, 4, 0, tzinfo=CET))
    assert len(bf._yield_notices_to_incidents([notice, _incident()], NOW)) == 2


def test_a_notice_without_an_end_stays() -> None:
    assert len(bf._yield_notices_to_incidents([_notice(ends_at=None), _incident()], NOW)) == 2


def test_a_notice_to_the_end_of_the_operating_day_gives_way() -> None:
    # WL's operating day ends 04:00 after the date.
    notice = _notice(ends_at=datetime(2026, 10, 27, 4, 0, tzinfo=CET))
    assert _titles(bf._yield_notices_to_incidents([notice, _incident()], NOW)) == ["1/2/71/D: Veranstaltung"]
    later = _notice(ends_at=datetime(2026, 10, 27, 4, 1, tzinfo=CET))
    assert len(bf._yield_notices_to_incidents([later, _incident()], NOW)) == 2


def test_the_chain_shows_the_incident_once() -> None:
    state: dict[str, Any] = {}
    items = bf._assemble_feed_items([_notice(), _incident()], state, NOW)
    assert _titles(items) == ["1/2/71/D: Veranstaltung"]


def test_an_incident_with_an_end_is_not_taken_into_the_notice() -> None:
    # 11.10.2026: "18: Laufveranstaltung" (I20261011-0007, "bis 23:55") went
    # into "18: LCC-Herbstmarathon am 11.10.2026" only because WL named an end.
    incident = _incident()
    incident["ends_at"] = datetime(2026, 10, 26, 18, 0, tzinfo=CET)
    assert bf._absorb_works_tickers([_notice(), incident]) == [_notice(), incident]
    items = bf._assemble_feed_items([_notice(), incident], {}, NOW)
    assert _titles(items) == ["1/2/71/D: Veranstaltung"]


def test_a_notice_of_weeks_still_takes_its_incident_with_an_end() -> None:
    incident = _incident()
    incident["ends_at"] = datetime(2026, 10, 26, 18, 0, tzinfo=CET)
    notice = _notice(ends_at=datetime(2026, 12, 5, 4, 0, tzinfo=CET))
    assert _titles(bf._absorb_works_tickers([notice, incident])) == ["1/2/71/D: Nationalfeiertag"]
