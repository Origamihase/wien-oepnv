"""Tickers of some lines of a longer WL incident take no slot of their own (2026-10-15).

Live on 2026-10-11 from 11:30 to 23:30 Vienna time (``docs/feed.xml``), a gas
leak in Brünner Straße took four of the ten slots: the long message
"30A/31A/36B: Gasrohrgebrechen" and one display ticker per line, "30A:
Gasrohrbruch", "36B: Gasrohrbruch", "31A: Gasrohrbruch" ("Kein Betrieb",
while the long message says the 31A runs a detour). The same day at 18:31,
"37/38/40/41/42: Schadhaftes Fahrzeug" stood beside "38/42: Schadhafter Zug"
and "37: Schadhafter Zug". The shapes below are WL cache entries of those
days (``cache/wl_9d709a/events.json``), read as the feed reads them.

Mutations checked against this file (each one caught, by the test named):

* the join is not called → ``test_the_gas_leak_takes_one_slot``.
* "Gasrohrbruch" is no synonym → ``test_the_gas_leak_takes_one_slot``.
* a line the text speaks of keeps its ticker's consequence → ``test_the_gas_leak_takes_one_slot``.
* a ticker of another cause joins → ``test_a_ticker_of_another_cause_keeps_its_slot``.
* the ticker may begin any time before the incident → ``test_a_ticker_older_than_the_incident_keeps_its_slot``.
* the validity is not compared → ``test_a_ticker_whose_validity_does_not_meet_keeps_its_slot``.
* the oldest incident owns the ticker → ``test_the_incident_begun_last_owns_the_ticker``.
* a line the text does not name loses its consequence → ``test_a_line_the_text_does_not_name_keeps_its_consequence``.
* a bare "Fahrtbehinderung" is added → ``test_a_bare_hindrance_adds_nothing``.
* the tickers' GUIDs are no members → ``test_the_tickers_become_members_of_the_entry``.
* a detour goes behind WL's stock sentence → ``test_a_detour_goes_in_front_of_the_stock_sentence``.
* a detour goes behind the cause → ``test_a_line_the_text_does_not_name_keeps_its_consequence``.
* what tells a rider nothing to do is dropped → ``test_what_tells_a_rider_nothing_to_do_comes_last``.
* the line of the stripped first label counts as unnamed → ``test_the_line_of_the_stripped_label_is_spoken_of``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest

import src.build_feed as bf
from src.feed.merge import deduplicate_fuzzy, member_guids
from src.feed_types import FeedItem

pytestmark = pytest.mark.usefixtures("time_line_today")


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(UTC)


def _wl(
    title: str, description: str, guid: str, start: str, end: str | None, *, number: str | None = None
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
    if number:
        item["_wl_ids"] = [number]
    return item


def _built(items: list[FeedItem]) -> list[FeedItem]:
    """What ``main`` makes of *items*: read, deduplicated, fuzzy-merged, merged."""
    read = cast(list[FeedItem], bf._normalize_item_datetimes(bf._post_filter_wl(list(items))))
    fuzzy = deduplicate_fuzzy(cast(list[dict[str, Any]], bf._dedupe_items(read)))
    return bf._merge_wl_ticker_clusters(cast(list[FeedItem], fuzzy))


def _shown(items: list[FeedItem]) -> list[tuple[str, str]]:
    return [(str(it["title"]), str(it["description"])) for it in _built(items)]


GAS_TEXT = (
    "Die Linie 36B wird in Richtung Jedlesee, Bellgasse zwischen Berzeliusgasse und Brünner Straße S über "
    "Brünner Straße umgeleitet. Die Linie 30A wird in Richtung Stammersdorf, Freiheitsplatz zwischen "
    "Berzeliusgasse und Gschweidlgasse über Brünner Straße, Hanreitergasse umgeleitet. Die Linie 31A wird in "
    "Richtung Großjedlersdorf, Jochbergengasse zwischen Berzeliusgasse und Großjedlersdorf, Jochbergengasse "
    "über Brünner Straße, Hanreitergasse, Jedlersdorfer Straße umgeleitet. Voraussichtliche Dauer: Nicht "
    "absehbar. Grund: Gasrohrgebrechen im Bereich Brünner Straße # Frauenstiftgasse ."
)
GAS = _wl("30A/31A/36B: Gasrohrgebrechen", GAS_TEXT, "26fb6f57", "2026-10-11T11:00:00+02:00",
          "2026-10-11T23:55:00+02:00", number="I20261011-0008")
GAS_TICKERS = [
    _wl("30A: Gasrohrbruch Betrieb ab Gschweidlgasse", "Gasrohrbruch\nBetrieb ab Gschweidlgasse", "a052426d",
        "2026-10-11T11:07:15+02:00", "2026-10-11T23:45:00+02:00"),
    _wl("31A: Gasrohrbruch Kein Betrieb", "Gasrohrbruch\nKein Betrieb", "eceeb9e8",
        "2026-10-11T11:04:44+02:00", "2026-10-11T23:45:00+02:00"),
    _wl("36B: Gasrohrbruch Betrieb ab Brünner Straße", "Gasrohrbruch\nBetrieb ab Brünner Straße <", "aba0800a",
        "2026-10-11T11:06:45+02:00", "2026-10-11T23:45:00+02:00"),
]


def test_the_gas_leak_takes_one_slot() -> None:
    # WL's text speaks of every line; "31A: Kein Betrieb" of one stop's
    # display would contradict "Die Linie 31A wird … umgeleitet".
    ((alone,),) = (_built([GAS]),)
    (item,) = _built([*GAS_TICKERS, GAS])
    assert (item["guid"], item["title"], item["description"]) == (
        "26fb6f57", "30A/31A/36B: Gasrohrgebrechen", alone["description"]
    )
    assert item["ends_at"] == _at("2026-10-11T23:55:00+02:00")


def test_the_tickers_become_members_of_the_entry() -> None:
    (item,) = _built([*GAS_TICKERS, GAS])
    assert member_guids(item) == sorted(["26fb6f57", "wl:I20261011-0008", "a052426d", "eceeb9e8", "aba0800a"])


def test_a_ticker_of_another_cause_keeps_its_slot() -> None:
    # 2026-10-09 11:48: a parked car on the 43, a minute into the rescue operation on 43/44.
    rescue = _wl("43/44: Rettungseinsatz",
                 "Linie 43: Betrieb nur zwischen Neuwaldegg und Alser Straße U. Linie 44: Betrieb nur zwischen "
                 "Ottakring, Maroltingergasse und Johann-Nepomuk-Berger-Platz. Grund: Rettungseinsatz im "
                 "Haltestellenbereich Lange Gasse.",
                 "f94fbeab", "2026-10-09T11:47:00+02:00", "2026-10-09T23:55:00+02:00", number="I20261009-0014")
    parked = _wl("43: Fahrtbehinderung Falschparker", "Fahrtbehinderung\nFalschparker", "45707199",
                 "2026-10-09T11:48:08+02:00", "2026-10-09T12:47:00+02:00")
    assert sorted(str(it["guid"]) for it in _built([rescue, parked])) == ["45707199", "f94fbeab"]


def test_a_ticker_older_than_the_incident_keeps_its_slot() -> None:
    old = _wl("31A: Gasrohrbruch Kein Betrieb", "Gasrohrbruch\nKein Betrieb", "old",
              "2026-10-11T10:49:00+02:00", "2026-10-11T23:45:00+02:00")
    assert len(_built([old, GAS])) == 2
    on_time = _wl("31A: Gasrohrbruch Kein Betrieb", "Gasrohrbruch\nKein Betrieb", "lead",
                  "2026-10-11T10:51:00+02:00", "2026-10-11T23:45:00+02:00")
    assert len(_built([on_time, GAS])) == 1


def test_a_ticker_whose_validity_does_not_meet_keeps_its_slot() -> None:
    ended = _wl("30A/31A/36B: Gasrohrgebrechen", GAS_TEXT, "ended", "2026-10-11T11:00:00+02:00",
                "2026-10-11T11:03:00+02:00", number="I20261011-0008")
    assert len(_built([GAS_TICKERS[0], ended])) == 2


# 2026-10-11 at 18:31: the 37 ticker began ten minutes before WL entered the incident.
TRAIN_TEXT = (
    "Linie 37: Betrieb nur zwischen Hohe Warte und Nußdorfer Straße U. Linie 38: Betrieb nur zwischen Grinzing "
    "und Spitalgasse. Weiterfahrt bis Alser Straße U. Linie 40: Betrieb nur zwischen Gersthof, Herbeckstraße und "
    "Währinger Straße, Volksoper U. Linie 41: Betrieb nur zwischen Pötzleinsdorf und Währinger Straße, Volksoper U. "
    "Linie 42: Betrieb nur zwischen Antonigasse und Währinger Straße, Volksoper U. Weichen Sie ersatzweise auf die "
    "Linien 43 und 44 aus. Voraussichtliche Dauer: 19:00 Uhr. Grund: Schadhaftes Fahrzeug im Haltestellenbereich "
    "Schwarzspanierstraße."
)
TRAIN = _wl("37/38/40/41/42: Schadhaftes Fahrzeug", TRAIN_TEXT, "da656eec", "2026-10-11T18:22:00+02:00",
            "2026-10-11T23:55:00+02:00", number="I20261011-0019")
TRAIN_TICKERS = [
    _wl("37: Fahrtbehinderung Schadhafter Zug", "Fahrtbehinderung\nSchadhafter Zug", "e1973a5b",
        "2026-10-11T18:12:27+02:00", "2026-10-11T19:30:00+02:00"),
    _wl("38: Fahrtbehinderung Schadhafter Zug", "Fahrtbehinderung\nSchadhafter Zug", "911c7a02",
        "2026-10-11T18:24:59+02:00", "2026-10-11T19:24:00+02:00"),
    _wl("38: Schadhafter Zug Betrieb ab Spitalgasse", "Schadhafter Zug\nBetrieb ab Spitalgasse", "560c2672",
        "2026-10-11T18:25:59+02:00", "2026-10-11T19:25:00+02:00"),
    _wl("42: Fahrtbehinderung Schadhafter Zug", "Fahrtbehinderung\nSchadhafter Zug", "4394f66a",
        "2026-10-11T18:28:58+02:00", "2026-10-11T19:28:00+02:00"),
    _wl("42: Schadhafter Zug Betrieb ab Währinger Straße, Volksoper",
        "Schadhafter Zug\nBetrieb ab Währinger Straße, Volksoper", "6406e8e8",
        "2026-10-11T18:29:29+02:00", "2026-10-11T19:28:00+02:00"),
]


def test_a_bare_hindrance_adds_nothing() -> None:
    # The 37 is the line whose label the feed strips; its ticker says only "Fahrtbehinderung".
    ((title, description),) = _shown([*TRAIN_TICKERS, TRAIN])
    assert title == "37/38/40/41/42: Schadhaftes Fahrzeug"
    assert "Fahrtbehinderung" not in description
    assert "Betrieb ab Spitalgasse" not in description


def test_a_line_the_text_does_not_name_keeps_its_consequence() -> None:
    text = "Die Linie 36B wird zwischen Berzeliusgasse und Brünner Straße S umgeleitet. Grund: Gasrohrgebrechen."
    gas = _wl("30A/31A/36B: Gasrohrgebrechen", text, "gas", "2026-10-11T11:00:00+02:00",
              "2026-10-11T23:55:00+02:00", number="I20261011-0008")
    ((_title, description),) = _shown([*GAS_TICKERS, gas])
    assert description == (
        "Die Linie 36B wird zwischen Berzeliusgasse und Brünner Straße S umgeleitet. "
        "30A: Betrieb ab Gschweidlgasse; 31A: Kein Betrieb. Grund: Gasrohrgebrechen."
    )


def test_the_incident_begun_last_owns_the_ticker() -> None:
    earlier = _wl("30A/32A: Gasrohrgebrechen", "Die Linie 32A wird umgeleitet. Grund: Gasrohrgebrechen.", "earlier",
                  "2026-10-11T09:00:00+02:00", "2026-10-11T23:55:00+02:00", number="I20261011-0001")
    built = _built([earlier, GAS_TICKERS[0], GAS])
    assert sorted(str(it["guid"]) for it in built) == ["26fb6f57", "earlier"]
    owner = next(it for it in built if it["guid"] == "26fb6f57")
    assert "a052426d" in member_guids(owner)


def test_a_detour_goes_in_front_of_the_stock_sentence() -> None:
    # 2026-10-06 18:30 Vienna: WL's text names no measure, the 14A display a detour.
    incident = _wl("13A/14A: Falschparker", "Linie 13A: Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen.",
                   "13a14a", "2026-10-06T17:44:00+02:00", "2026-10-06T23:55:00+02:00", number="I20261006-0033")
    detour = _wl("14A: Falschparker Umleitung über Gumpendorfer Str., Linke Wienzeile",
                 "Falschparker\nUmleitung über Gumpendorfer Str., Linke Wienzeile", "14a",
                 "2026-10-06T17:53:00+02:00", "2026-10-06T18:53:00+02:00")
    assert _shown([incident, detour]) == [(
        "13A/14A: Falschparker",
        "14A: Umleitung über Gumpendorfer Str., Linke Wienzeile. "
        "Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen.",
    )]


def test_what_tells_a_rider_nothing_to_do_comes_last() -> None:
    text = "Die Linie 36B wird zwischen Berzeliusgasse und Brünner Straße S umgeleitet. Grund: Gasrohrgebrechen."
    gas = _wl("30A/31A/36B: Gasrohrgebrechen", text, "gas", "2026-10-11T11:00:00+02:00",
              "2026-10-11T23:55:00+02:00", number="I20261011-0008")
    waiting = _wl("31A: Längere Wartezeiten", "Gasrohrbruch\nLängere Wartezeiten", "wait",
                  "2026-10-11T11:04:44+02:00", "2026-10-11T23:45:00+02:00")
    ((_title, description),) = _shown([gas, waiting])
    assert description == f"{text} 31A: Längere Wartezeiten."


def test_the_line_of_the_stripped_label_is_spoken_of() -> None:
    # 2026-10-19 18:31: reading strips "Linie 2:", so the text names only the
    # 10, yet its first sentence is the 2's detour; the display tickers of
    # the 2 ("Betrieb ab Mayssengasse") add nothing to it.
    accident = _wl("2/10: Fremder Verkehrsunfall",
                   "Linie 2: Umleitung in beiden Richtungen zwischen Dornbach und Johann-Nepomuk-Berger-Platz "
                   "über die Strecke der Linien 43 und 9. Linie 10: Betrieb nur zwischen Unter St. Veit, "
                   "Hummelgasse und Ottakring, Maroltingergasse. Voraussichtliche Dauer: 19:45 Uhr. Grund: "
                   "Fremder Verkehrsunfall im Bereich Wilhelminenstraße # Sandleitengasse.",
                   "b1e7c767", "2026-10-19T18:31:00+02:00", "2026-10-19T23:55:00+02:00",
                   number="I20261019-0041")
    tickers = [
        _wl("10: Fremdunfall Betrieb ab Maroltingergasse", "Fremdunfall\nBetrieb ab Maroltingergasse",
            "48318e15", "2026-10-19T18:35:04+02:00", "2026-10-19T19:34:00+02:00"),
        _wl("2: Fremdunfall Betrieb ab Mayssengasse", "Fremdunfall\nBetrieb ab Mayssengasse",
            "f4be783b", "2026-10-19T18:36:31+02:00", "2026-10-19T21:45:00+02:00"),
        _wl("2: Fremdunfall Züge halten bei Linie 9", "Fremdunfall\nZüge halten bei Linie 9",
            "17df4479", "2026-10-19T18:37:01+02:00", "2026-10-19T21:00:00+02:00"),
    ]
    ((alone,),) = (_built([accident]),)
    (item,) = _built([*tickers, accident])
    assert item["description"] == alone["description"]
    assert "Mayssengasse" not in item["description"]
