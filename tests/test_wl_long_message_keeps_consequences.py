"""A long message keeps what its tickers add (audit 2026-10-01, open point 1).

Since 2026-09-27 a long message that says more than WL's stock sentence
stands for its tickers. Within its window it dropped them, also what they
said and it did not. Live on 2026-10-01 05:32: "Betrieb ab Mühlbreiten" of
64A stood only under the mangled title "64A: Verkehrsunfall & Verkehrsunfall";
after #1909 it stood nowhere. Over 756 WL cache versions 29 groups lost a
ticker's consequence this way, about 19 of them a real one ("Umleitung zur
Wattgasse, 10A ausweichen", "Shuttlebus eingerichtet, …").

Mutations checked against this file (each one caught, by the test named):

* the tickers are dropped as before → ``test_64a_keeps_betrieb_ab_muehlbreiten``.
* "Fahrtbehinderung" is added → ``test_64a_keeps_betrieb_ab_muehlbreiten``.
* WL's display markup stays → ``test_wl_markup_does_not_reach_the_feed``.
* a longer form does not count as said → ``test_a_ticker_that_only_restates_adds_nothing``.
* a ticker of another cause is added → ``test_a_ticker_of_another_cause_stays_out``.
* what tells a rider nothing to do comes first → ``test_what_tells_a_rider_nothing_comes_last``.
* advice to take another line counts as nothing to do → ``test_advice_to_take_another_line_stays_in_front``.
* a negation counts as news → ``test_a_negation_in_other_words_adds_nothing``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import src.build_feed as bf
from src.feed.merge import deduplicate_fuzzy
from src.feed_types import FeedItem


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(UTC)


def _wl(title: str, description: str, guid: str, start: str, end: str) -> FeedItem:
    return {
        "source": "Wiener Linien",
        "category": "Störung",
        "title": title,
        "description": description,
        "link": "https://www.wienerlinien.at/ogd_realtime",
        "guid": guid,
        "pubDate": _at(start),
        "starts_at": _at(start),
        "ends_at": _at(end),
        "_identity": f"wl|{guid}",
    }


def _shown(items: list[FeedItem]) -> list[tuple[str, str]]:
    read = cast(list[FeedItem], bf._normalize_item_datetimes(bf._post_filter_wl(list(items))))
    fuzzy = deduplicate_fuzzy(cast(list[dict[str, Any]], bf._dedupe_items(read)))
    return [(str(it["title"]), str(it["description"])) for it in bf._merge_wl_ticker_clusters(cast(list[FeedItem], fuzzy))]


def test_64a_keeps_betrieb_ab_muehlbreiten() -> None:
    # The cache of 2026-10-01 05:32, all three in one window.
    items = [
        _wl("64A: Verkehrsunfall", "Linie 64A: Unregelmäßige Intervalle in beiden Richtungen. Grund: Verkehrsunfall.",
            "2b43b2cf", "2026-10-01T07:01:00+02:00", "2026-10-01T23:55:00+02:00"),
        _wl("64A: Verkehrsunfall Betrieb ab Mühlbreiten", "Verkehrsunfall\nBetrieb ab Mühlbreiten",
            "55edf8b6", "2026-10-01T07:02:57+02:00", "2026-10-01T08:02:00+02:00"),
        _wl("64A: Fahrtbehinderung Verkehrsunfall", "Fahrtbehinderung\nVerkehrsunfall",
            "211d0a75", "2026-10-01T07:03:27+02:00", "2026-10-01T08:14:00+02:00"),
    ]
    # The text names no measure of its own, so the ticker's comes first;
    # a bare "Fahrtbehinderung" adds nothing.
    assert _shown(items) == [
        ("64A: Verkehrsunfall", "Betrieb ab Mühlbreiten. Unregelmäßige Intervalle in beiden Richtungen. Grund: Verkehrsunfall.")
    ]


def test_a_ticker_that_only_restates_adds_nothing() -> None:
    # 9 on 2026-09-18: "**Verspätung** / Schadhaftes Fahrzeug" under "9: Verspätungen".
    items = [
        _wl("9: Verspätungen", "Linie 9: Unregelmäßige Intervalle in beiden Richtungen. Grund: Schadhaftes Fahrzeug.",
            "31a26903", "2026-09-18T05:22:00+02:00", "2026-09-18T23:55:00+02:00"),
        _wl("9: **Verspätung** Schadhaftes Fahrzeug", "**Verspätung**\nSchadhaftes Fahrzeug",
            "a7f3b2ea", "2026-09-18T05:23:42+02:00", "2026-09-18T06:22:00+02:00"),
    ]
    assert _shown(items) == [
        ("9: Verspätungen", "Unregelmäßige Intervalle in beiden Richtungen. Grund: Schadhaftes Fahrzeug.")
    ]


def test_wl_markup_does_not_reach_the_feed() -> None:
    items = [
        _wl("9: Verspätungen", "Linie 9: Unregelmäßige Intervalle in beiden Richtungen. Grund: Schadhaftes Fahrzeug.",
            "31a26903", "2026-09-18T05:22:00+02:00", "2026-09-18T23:55:00+02:00"),
        _wl("9: **Umleitung** über Gersthof", "**Umleitung**\nüber Gersthof",
            "markup", "2026-09-18T05:23:42+02:00", "2026-09-18T06:22:00+02:00"),
    ]
    assert _shown(items) == [
        (
            "9: Verspätungen",
            "Umleitung über Gersthof. Unregelmäßige Intervalle in beiden Richtungen. Grund: Schadhaftes Fahrzeug.",
        )
    ]


def test_a_ticker_of_another_cause_stays_out() -> None:
    # 48A on 2026-09-27: beside "Grund: Fremder Verkehrsunfall" a ticker of
    # another cause read as a second cause of the same incident.
    items = [
        _wl("48A: Fremder Verkehrsunfall",
            "Linie 48A: Fahrtbehinderung in Richtung Klinik Penzing. Voraussichtliche Dauer: 06:00 Uhr. "
            "Grund: Fremder Verkehrsunfall im Bereich Lerchenfelder Gürtel.",
            "7da5c613", "2026-09-27T05:26:00+02:00", "2026-09-27T23:55:00+02:00"),
        _wl("48A: Falschparker Betrieb ab Burggasse", "Falschparker\nBetrieb ab Burggasse",
            "05cf9172", "2026-09-27T05:26:17+02:00", "2026-09-27T06:26:00+02:00"),
    ]
    ((title, description),) = _shown(items)
    assert title == "48A: Fremder Verkehrsunfall"
    assert "Burggasse" not in description


def test_what_tells_a_rider_nothing_comes_last() -> None:
    # N29 on 2026-09-30: in front, "Derzeit längere Wartezeiten!" took the
    # second sentence of the summary, and with it the cause.
    items = [
        _wl("N29: Verspätungen", "Linie N29: Unregelmäßige Intervalle in beiden Richtungen. Grund: Betriebsstörung.",
            "4bf68d39", "2026-09-30T00:13:00+02:00", "2026-09-30T04:40:00+02:00"),
        _wl("N29: Derzeit längere Wartezeiten!", "Derzeit längere\nWartezeiten!",
            "990a64a0", "2026-09-30T00:13:24+02:00", "2026-09-30T04:45:00+02:00"),
    ]
    assert _shown(items) == [
        (
            "N29: Verspätungen",
            "Unregelmäßige Intervalle in beiden Richtungen. Grund: Betriebsstörung. Derzeit längere Wartezeiten!",
        )
    ]


def test_a_negation_in_other_words_adds_nothing() -> None:
    # 1A on 2026-09-22: "Kein Betrieb" beside "Derzeit ist ein Betrieb nicht möglich".
    items = [
        _wl("1A: Polizeiübung Kein Betrieb", "Polizeiübung\nKein Betrieb",
            "b80b7692", "2026-09-22T09:54:35+02:00", "2026-09-22T10:53:00+02:00"),
        _wl("1A: Polizeiübung",
            "Linie 1A: Derzeit ist ein Betrieb nicht möglich. Voraussichtliche Dauer: 11:00 Uhr. "
            "Grund: Polizeiübung im Bereich Herrengasse.",
            "7b2b5110", "2026-09-22T09:55:00+02:00", "2026-09-22T23:55:00+02:00"),
    ]
    ((title, description),) = _shown(items)
    assert description == (
        "Derzeit ist ein Betrieb nicht möglich. Voraussichtliche Dauer: 11:00 Uhr. "
        "Grund: Polizeiübung im Bereich Herrengasse."
    )


def test_advice_to_take_another_line_stays_in_front() -> None:
    # 27 on 2026-09-29: "Linie 25 in Tokiostraße benützen" is what a rider has to do.
    items = [
        _wl("27: Signalstörung", "Linie 27: Unregelmäßige Intervalle in beiden Richtungen. Grund: Signalstörung.",
            "27-long", "2026-09-29T21:20:00+02:00", "2026-09-29T23:55:00+02:00"),
        _wl("27: Signalstörung Linie 25 in Tokiostraße benützen", "Signalstörung\nLinie 25 in Tokiostraße benützen",
            "27-a", "2026-09-29T21:21:00+02:00", "2026-09-29T22:20:00+02:00"),
    ]
    ((_, description),) = _shown(items)
    assert description.startswith("Linie 25 in Tokiostraße benützen. Unregelmäßige Intervalle")
