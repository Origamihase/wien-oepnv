"""A display that names the long message's incident in another word (2026-11-06).

Live on 2026-11-05 at 18:30 Vienna time the feed read "12: Polizeieinsatz"
over "Unregelmäßige Intervalle in beiden Richtungen." while WL's displays
said "Verkehrsunfall Betrieb ab Spitalgasse" and "Verkehrsunfall Züge halten
bei Linie 37-42" (check of the KI watcher's report, 2026-11-06). Within a
publication window a telling long message stood for its incident, and a
ticker of another cause stayed out whole, with its measure (48A on
2026-09-27: "Falschparker" beside "Grund: Fremder Verkehrsunfall"). WL cache
since 2026-10-16: 147 windows with a long message, 17 with a display cause
beside it, 5 of them with a measure.

Now the display's measure stays and its word stays out (``_announced``). A
window holds two things only where a message of another cause is a long
message itself, or where one names a planned measure and the other an
incident (``_another_incident``): an incident never joins planned works
(operator decision 2026-11-03). The shapes below are real cache entries.

Mutations checked against this file (each one caught, by the test named):

* the other cause's ticker stays out whole → ``test_the_display_measures_stay_under_the_long_message``.
* the window is split by cause → ``test_the_display_measures_stay_under_the_long_message`` (the title).
* the display's cause word is added → ``test_a_bare_display_cause_adds_nothing``, ``test_a_generic_display_word_keeps_its_measure``.
* an incident beside planned works stays in the works' window → ``test_an_incident_beside_planned_works_keeps_its_slot``.
* the works' stop beside an incident is dropped → ``test_the_works_stop_beside_an_incident_keeps_its_slot``.
* a second long message stays in the window → ``test_a_second_long_message_splits_the_window``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import src.build_feed as bf
from src.feed.merge import deduplicate_fuzzy
from src.feed_types import FeedItem


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(UTC)


def _wl(
    title: str,
    description: str,
    guid: str,
    start: str,
    end: str | None = None,
    *,
    wl_id: str | None = None,
) -> FeedItem:
    item: FeedItem = {
        "source": "Wiener Linien",
        "category": "Störung",
        "title": title,
        "description": description,
        "link": "https://www.wienerlinien.at/ogd_realtime",
        "guid": guid,
        "pubDate": _at(start),
        "starts_at": _at(start),
        "ends_at": _at(end) if end else None,
        "_identity": f"wl|{guid}",
    }
    if wl_id:
        item["_wl_ids"] = [wl_id]
        item["_incident_start"] = _at(start)
    return item


def _read(items: list[FeedItem]) -> list[FeedItem]:
    return cast(list[FeedItem], bf._normalize_item_datetimes(bf._post_filter_wl(list(items))))


def _merged(items: list[FeedItem]) -> list[FeedItem]:
    fuzzy = deduplicate_fuzzy(cast(list[dict[str, Any]], bf._dedupe_items(_read(items))))
    return bf._merge_wl_ticker_clusters(cast(list[FeedItem], fuzzy))


def _shown(items: list[FeedItem]) -> list[tuple[str, str]]:
    return [(str(it["title"]), str(it["description"])) for it in _merged(items)]


# Line 12 on 2026-11-05: the displays' "Verkehrsunfall" and the long message's "Polizeieinsatz", one incident.
LINE_12 = [
    _wl("12: Fahrtbehinderung Verkehrsunfall", "Fahrtbehinderung\nVerkehrsunfall",
        "384fc092", "2026-11-05T17:51:33+01:00", "2026-11-05T18:56:00+01:00"),
    _wl("12: Fahrtbehinderung wegen Polizeieinsatz",
        "Linie 12: Unregelmäßige Intervalle in beiden Richtungen. Grund: Polizeieinsatz.",
        "72bf49ba", "2026-11-05T17:57:05+01:00", wl_id="I20261105-0044"),
    _wl("12: Verkehrsunfall Betrieb ab Spitalgasse", "Verkehrsunfall\nBetrieb ab Spitalgasse",
        "ddd5b48c", "2026-11-05T17:57:05+01:00", "2026-11-05T18:56:00+01:00"),
    _wl("12: Verkehrsunfall Züge halten Züge halten bei LInie 37-42", "Verkehrsunfall\nZüge halten Züge halten bei LInie 37-42",
        "638096f2", "2026-11-05T17:57:35+01:00", "2026-11-05T18:56:00+01:00"),
]


def test_the_display_measures_stay_under_the_long_message() -> None:
    assert bf._split_by_cause(_read(LINE_12), [[0, 1, 2, 3]]) == [[0, 1, 2, 3]]
    (item,) = _merged(LINE_12)
    assert (item["title"], item["description"]) == (
        "12: Fahrtbehinderung wegen Polizeieinsatz",
        "Betrieb ab Spitalgasse; Züge halten Züge halten bei LInie 37-42. "
        "Unregelmäßige Intervalle in beiden Richtungen. Grund: Polizeieinsatz.",
    )
    xml = bf._make_rss([item], _at("2026-11-05T18:31:16+01:00"), {}, lang="de")
    assert "<![CDATA[12: Polizeieinsatz]]>" in xml
    assert "Betrieb ab Spitalgasse" in xml
    assert "Verkehrsunfall" not in xml


def test_a_generic_display_word_keeps_its_measure() -> None:
    # Line 2 on 2026-11-05: "Betriebsstörung" on the display, "Fahrleitungsgebrechen" in the long message.
    items = [
        _wl("2: Beschädigte Oberleitung",
            "Die Linie 2 kann die Haltestelle Josefstädter Straße in Richtung Friedrich-Engels-Platz nicht einhalten."
            "Voraussichtliche Dauer: 13:00 Uhr.Grund: Fahrleitungsgebrechen.",
            "0b0284d7", "2026-11-05T11:46:00+01:00", wl_id="I20261105-0020"),
        _wl("2: Betriebsstörung Betrieb ab Albertgasse", "Betriebsstörung\nBetrieb ab Albertgasse",
            "ff30e130", "2026-11-05T11:49:56+01:00", "2026-11-05T12:55:00+01:00"),
    ]
    ((title, description),) = _shown(items)
    assert title == "2: Beschädigte Oberleitung"
    assert description.startswith(
        "Betrieb ab Albertgasse. Die Linie 2 kann die Haltestelle Josefstädter Straße in Richtung Friedrich-Engels-Platz"
    )
    assert "Betriebsstörung" not in description


def test_a_bare_display_cause_adds_nothing() -> None:
    # 48A on 2026-09-27 05:26: the display's "Falschparker" names no measure.
    long_message = _wl(
        "48A: Fremder Verkehrsunfall",
        "Linie 48A: Fahrtbehinderung in Richtung Klinik Penzing. Voraussichtliche Dauer: 06:00 Uhr. "
        "Grund: Fremder Verkehrsunfall im Bereich Lerchenfelder Gürtel.",
        "7da5c613", "2026-09-27T05:26:00+02:00", "2026-09-27T23:55:00+02:00", wl_id="I20260927-0001",
    )
    ticker = _wl("48A: Fahrtbehinderung Falschparker", "Fahrtbehinderung\nFalschparker",
                 "05cf9172", "2026-09-27T05:26:17+02:00", "2026-09-27T06:26:00+02:00")
    (alone,) = _read([long_message])
    assert _shown([long_message, ticker]) == [(str(alone["title"]), str(alone["description"]))]


# Line 1 on 2026-10-20: the long message of the works, an incident six minutes later.
LINE_1 = [
    _wl("1: Gleisbauarbeiten",
        "Linie 1: Betrieb nur zwischen Prater Hauptallee und Knöllgasse. Weiterfahrt zum Reumannplatz U. "
        "Ersatz: Linien 7A und 65A. Voraussichtliche Dauer: Mitte Dezember 2026. "
        "Grund: Gleisbauarbeiten im Bereich Stefan-Fadinger-Platz.",
        "c5c8c5c0", "2026-10-20T11:58:00+02:00", "2026-12-05T01:00:00+01:00", wl_id="I20261020-0029"),
    _wl("1: Rettungseinsatz Betrieb ab Matzleinsdorfer Platz", "Rettungseinsatz\nBetrieb ab Matzleinsdorfer Platz",
        "6e5c6502", "2026-10-20T12:04:07+02:00", "2026-10-21T16:12:00+02:00"),
]


def test_an_incident_beside_planned_works_keeps_its_slot() -> None:
    assert bf._split_by_cause(_read(LINE_1), [[0, 1]]) == [[0], [1]]
    shown = _shown(LINE_1)
    # The running incident in front, the planned works behind it (operator decision 2026-11-03).
    assert [title for title, _ in shown] == ["1: Rettungseinsatz Betrieb ab Matzleinsdorfer Platz", "1: Gleisbauarbeiten"]
    incident, works = (description for _, description in shown)
    assert "Matzleinsdorfer" not in works
    assert "Matzleinsdorfer Platz" in incident


def test_the_works_stop_beside_an_incident_keeps_its_slot() -> None:
    # N8 on 2026-10-30 00:45:16: the works' stop and the fire brigade's detour in the same second.
    items = [
        _wl("N8: Busse halten Aßmayergasse nach Flurschützstraße bei Linie 59A",
            "Gleisbauarbeiten\nBusse halten Aßmayergasse nach Flurschützstraße bei Linie 59A",
            "10b9a6d5", "2026-10-30T00:45:16+01:00", "2026-10-30T05:15:00+01:00"),
        _wl("N8: Feuerwehreinsatz Betrieb ab Bhf. Meidling",
            "Die Linie N8 wird in beiden Richtungen zwischen Bhf. Meidling S U, Schedifkaplatz und Hans-Mandl-Berufsschule "
            "über Eichenstraße und Längenfeldgasse umgeleitet. Voraussichtliche Dauer: Nicht absehbar. "
            "Grund: Feuerwehreinsatz im Haltestellenbereich Hans-Mandl-Berufsschule.",
            "db388975", "2026-10-30T00:45:16+01:00", wl_id="I20261030-0004"),
    ]
    assert sorted(bf._split_by_cause(_read(items), [[0, 1]])) == [[0], [1]]
    shown = _shown(items)
    assert len(shown) == 2
    (fire,) = (description for title, description in shown if "Feuerwehreinsatz" in title)
    (works,) = (description for title, description in shown if "Feuerwehreinsatz" not in title)
    assert "Aßmayergasse" not in fire
    assert "Aßmayergasse" in works


def test_a_second_long_message_splits_the_window() -> None:
    # Two incidents with their own WL numbers nine minutes apart (shape of the 71 on 2026-10-29).
    items = [
        _wl("71: Rettungseinsatz", "Linie 71: Betrieb nur zwischen Börse und Zentralfriedhof 3. Tor. Grund: Rettungseinsatz.",
            "a1a1a1a1", "2026-10-29T18:31:00+01:00", wl_id="I20261029-0032"),
        _wl("71: Polizeieinsatz",
            "Linie 71: Fahrtbehinderung in Richtung Börse. Voraussichtliche Dauer: 19:30 Uhr. Grund: Polizeieinsatz.",
            "a2a2a2a2", "2026-10-29T18:40:00+01:00", wl_id="I20261029-0034"),
    ]
    assert bf._split_by_cause(_read(items), [[0, 1]]) == [[0], [1]]
    ((title, description),) = _shown(items)
    assert title == "71: Polizeieinsatz, Rettungseinsatz"
    assert "Polizeieinsatz:" in description and "Rettungseinsatz:" in description
