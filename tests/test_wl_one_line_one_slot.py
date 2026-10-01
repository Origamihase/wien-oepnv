"""One line, one slot (operator decision 2026-10-01).

"Wenn mehrere unterschiedliche Linien betroffen sind, soll die Störung auch
angezeigt werden. Mehrere Störungsmeldungen zur selben Linie sollte so gut
wie möglich zusammengefasst werden."

Live on 2026-10-01, ``docs/feed.xml`` (19:01): "60: Schadhafter Pkw &
Schadhafter Pkw Betrieb ab Anschützgasse" over "Fahrtbehinderung". The fuzzy
merge joined two tickers of line 60 before the WL merge saw them. And "7A:
Verkehrsüberlastung" stood beside "7A: Busse halten Laxenburger Straße 66".
The shapes below are WL cache entries of that day
(``cache/wl_9d709a/events.json``), read as the feed reads them.

Mutations checked against this file (each one caught, by the test named):

* the fuzzy merge joins WL disruptions of the same line again → ``test_two_tickers_of_line_60_read_as_one``.
* it no longer joins those of overlapping lines → ``test_overlapping_lines_still_merge_in_the_fuzzy_step``.
* the validity is not compared → ``test_incidents_that_do_not_meet_keep_their_slots``.
* the lines are not compared → ``test_different_lines_keep_their_slots``.
* the oldest incident leads → ``test_two_causes_of_line_66a_share_one_slot``.
* a combined entry counts as a ticker → ``test_two_causes_of_line_66a_share_one_slot``.
* a sentence-long cause is listed → ``test_a_standing_message_joins_no_incident``.
* a group without a cause joins the oldest incident → ``test_a_message_without_cause_joins_the_nearest_incident``.
* WL's synonyms are not resolved → ``test_wl_synonyms_are_one_incident``.
* the oldest long message stands, or all are merged → ``test_the_newest_long_message_stands_for_its_incident``.
* a standing long message drops the stops beside it → ``test_a_standing_long_message_keeps_the_stops_of_its_line``.
* the tickers go first beside a long message that names a consequence → ``test_a_synonym_ticker_keeps_what_the_long_message_does_not_say``.
* a bare "Fahrtbehinderung" stands for an incident → ``test_a_bare_hindrance_gives_way_to_the_long_messages_sentence``.
* the stock sentence pushes out the display's consequence → ``test_the_display_names_what_the_stock_sentence_does_not``.
* the stock sentence keeps its place beside something concrete → ``test_the_stock_sentence_gives_way_in_a_combined_entry``.
* nothing is cut to fit 180 characters → ``test_a_long_combined_entry_gives_up_its_last_consequences_first``.
* the shared opening is not shared → ``test_consequences_that_open_alike_share_the_opening``.
* a consequence with its own comma is folded in → ``test_consequences_that_open_alike_share_the_opening``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import src.build_feed as bf
from src.feed.merge import deduplicate_fuzzy
from src.feed_types import FeedItem


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(UTC)


def _wl(title: str, description: str, guid: str, start: str, end: str | None, *, published: str | None = None) -> FeedItem:
    return {
        "source": "Wiener Linien",
        "category": "Störung",
        "title": title,
        "description": description,
        "link": "https://www.wienerlinien.at/ogd_realtime",
        "guid": guid,
        "pubDate": _at(published or start),
        "starts_at": _at(start),
        "ends_at": _at(end) if end else None,
        "_identity": f"wl|{guid}",
    }


def _built(items: list[FeedItem]) -> list[FeedItem]:
    """What ``main`` makes of *items*: read, deduplicated, fuzzy-merged, merged."""
    read = cast(list[FeedItem], bf._normalize_item_datetimes(bf._post_filter_wl(list(items))))
    fuzzy = deduplicate_fuzzy(cast(list[dict[str, Any]], bf._dedupe_items(read)))
    return bf._merge_wl_ticker_clusters(cast(list[FeedItem], fuzzy))


def _shown(items: list[FeedItem]) -> list[tuple[str, str]]:
    return [(str(it["title"]), str(it["description"])) for it in _built(items)]


LINE_60 = [
    _wl("60: Fahrtbehinderung Schadhafter Pkw", "Fahrtbehinderung\nSchadhafter Pkw", "eb0220c8",
        "2026-10-01T18:50:36+02:00", "2026-10-01T19:50:00+02:00"),
    _wl("60: Schadhafter Pkw Betrieb ab Anschützgasse", "Schadhafter Pkw\nBetrieb ab Anschützgasse", "3145b3c4",
        "2026-10-01T18:51:06+02:00", "2026-10-01T19:50:00+02:00"),
]


def test_two_tickers_of_line_60_read_as_one() -> None:
    assert _shown(LINE_60) == [("60: Schadhafter Pkw", "Fahrtbehinderung; Betrieb ab Anschützgasse.")]
    xml = bf._make_rss(_built(LINE_60), _at("2026-10-01T19:01:00+02:00"), {}, lang="de")
    assert "<![CDATA[60: Schadhafter Pkw]]>" in xml
    assert " & " not in xml


def test_overlapping_lines_still_merge_in_the_fuzzy_step() -> None:
    # The WL merge joins equal line sets only; 40/41 and 40 stay the fuzzy merge's.
    items = [
        _wl("40/41: Betrieb ab Gersthof", "Betrieb ab Gersthof", "a", "2026-05-20T08:06:12+02:00", None),
        _wl("40: Falschparker Betrieb ab Gersthof", "Falschparker\nBetrieb ab Gersthof", "b",
            "2026-05-20T08:07:00+02:00", None),
    ]
    assert len(_built(items)) == 1


# 18 on 2026-10-01: four tickers of one incident over twelve minutes.
LINE_18 = [
    _wl("18: Gleisschaden", "Linie 18: Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen.",
        "18-long", "2026-10-01T16:25:00+02:00", "2026-10-01T23:55:00+02:00"),
    _wl("18: Gleisschaden Betrieb ab Ludwig-Koeßler-Platz", "Gleisschaden\nBetrieb ab Ludwig-Koeßler-Platz",
        "18-a", "2026-10-01T16:31:52+02:00", "2026-10-01T17:31:00+02:00"),
    _wl("18: Fahrtbehinderung Gleisschaden", "Fahrtbehinderung\nGleisschaden",
        "18-b", "2026-10-01T16:33:53+02:00", "2026-10-01T17:33:00+02:00"),
    _wl("18: Gleisschaden Einstieg Schnirchgasse", "Gleisschaden\nEinstieg Schnirchgasse",
        "18-c", "2026-10-01T16:37:23+02:00", "2026-10-01T17:37:00+02:00"),
]


def test_an_incident_longer_than_its_window_is_one_entry() -> None:
    (item,) = _built(LINE_18)
    assert (item["guid"], item["title"], item["description"]) == (
        "18-long",
        "18: Gleisschaden",
        "Betrieb ab Ludwig-Koeßler-Platz; Einstieg Schnirchgasse.",
    )


# Since July in the cache: a disruption whose title is a sentence.
STADIONBRUECKE = _wl(
    "18: Haltestelle Stadionbrücke im Rahmen des Straßenbahn-Neubaus der Linie 18 aufgelassen. "
    "Bitte auf nahegelegene Haltestellen ausweichen.",
    "Haltestelle Stadionbrücke im Rahmen des Straßenbahn-Neubaus der Linie 18 aufgelassen. "
    "Bitte auf nahegelegene Haltestellen ausweichen.",
    "7aa6585f", "2026-07-13T14:07:00+02:00", "2026-12-31T14:07:00+01:00",
)


def test_a_standing_message_joins_no_incident() -> None:
    titles = sorted(str(it["title"]) for it in _built([STADIONBRUECKE, *LINE_18]))
    assert titles == ["18: Gleisschaden", str(STADIONBRUECKE["title"])]


# 66A on 2026-10-01: a planned stop relocation and a live incident.
SALVATORIANERPLATZ = _wl(
    "66A: Busse halten Salvatorianerplatz", "Bauarbeiten\nBusse halten Salvatorianerplatz", "f420c43d",
    "2026-10-01T04:40:13+02:00", "2026-10-02T01:00:00+02:00",
)
RESCUE_66A = _wl(
    "66A: Rettungseinsatz", "Linie 66A: Unregelmäßige Intervalle in beiden Richtungen. Grund: Rettungseinsatz.",
    "2be107df", "2026-10-01T09:52:00+02:00", "2026-10-01T23:55:00+02:00",
)


def test_two_causes_of_line_66a_share_one_slot() -> None:
    (item,) = _built([SALVATORIANERPLATZ, RESCUE_66A])
    # The newest incident leads: its GUID keeps the entry at the top of the feed.
    assert (item["guid"], item["title"], item["description"]) == (
        "2be107df",
        "66A: Rettungseinsatz, Bauarbeiten",
        "Rettungseinsatz: Unregelmäßige Intervalle in beiden Richtungen. "
        "Bauarbeiten: Busse halten Salvatorianerplatz.",
    )
    assert (item["starts_at"], item["ends_at"]) == (RESCUE_66A["starts_at"], SALVATORIANERPLATZ["ends_at"])
    # Its title lists causes; the feed must not split it like a ticker's.
    assert not bf._is_wl_ticker(item)


def test_the_feed_shows_both_causes() -> None:
    xml = bf._make_rss(_built([SALVATORIANERPLATZ, RESCUE_66A]), _at("2026-10-01T10:01:00+02:00"), {}, lang="de")
    assert xml.count("<item>") == 1
    assert "<![CDATA[66A: Rettungseinsatz, Bauarbeiten]]>" in xml
    assert (
        "Rettungseinsatz: Unregelmäßige Intervalle in beiden Richtungen. "
        "Bauarbeiten: Busse halten Salvatorianerplatz. [01.10.2026\u202f–\u202f02.10.2026]"
    ) in xml


def test_incidents_that_do_not_meet_keep_their_slots() -> None:
    ended = _wl("66A: Rettungseinsatz", "Linie 66A: Unregelmäßige Intervalle in beiden Richtungen. Grund: Rettungseinsatz.",
                "early", "2026-10-01T02:00:00+02:00", "2026-10-01T03:00:00+02:00")
    assert len(_built([SALVATORIANERPLATZ, ended])) == 2


def test_different_lines_keep_their_slots() -> None:
    # Operator: where different lines are affected, each shows.
    items = [
        _wl("1A: Demonstration", "Linie 1A: Kein Betrieb. Grund: Demonstration.", "1a",
            "2026-10-01T17:00:00+02:00", "2026-10-01T21:00:00+02:00"),
        _wl("3A: Demonstration", "Linie 3A: Betrieb ab Hoher Markt. Grund: Demonstration.", "3a",
            "2026-10-01T17:00:00+02:00", "2026-10-01T21:00:00+02:00"),
    ]
    assert [it["title"] for it in _built(items)] == ["1A: Demonstration", "3A: Demonstration"]


def test_a_message_without_cause_joins_the_nearest_incident() -> None:
    # 3A on 2026-10-01: the network change came with a stop at midnight, the demonstration at 16:30.
    items = [
        _wl("3A: Netzänderung Betrieb ab Riemergasse", "Netzänderung\nBetrieb ab Riemergasse", "net",
            "2026-10-01T00:00:10+02:00", "2026-10-02T01:00:00+02:00"),
        _wl("3A: Busse halten Kärntner Ring 5", "Busse halten\nKärntner Ring 5", "stop",
            "2026-10-01T00:20:10+02:00", "2026-10-02T01:00:00+02:00"),
        _wl("3A: Demonstration Betrieb ab Hoher Markt", "Demonstration\nBetrieb ab Hoher Markt", "demo",
            "2026-10-01T16:30:00+02:00", "2026-10-01T21:00:00+02:00"),
        _wl("3A: Busse halten Rotenturmstraße", "Busse halten\nRotenturmstraße", "stop2",
            "2026-10-01T16:41:00+02:00", "2026-10-01T21:00:00+02:00"),
    ]
    assert _shown(items) == [
        (
            "3A: Demonstration, Netzänderung",
            "Demonstration: Betrieb ab Hoher Markt; Busse halten Rotenturmstraße. "
            "Netzänderung: Betrieb ab Riemergasse; Busse halten Kärntner Ring 5.",
        )
    ]


def test_wl_synonyms_are_one_incident() -> None:
    # In one window WL wrote "Schadhafter Zug" on the display and
    # "Schadhaftes Fahrzeug" in the long message 14 times since September.
    items = [
        _wl("60: Schadhafter Zug Betrieb ab Anschützgasse", "Schadhafter Zug\nBetrieb ab Anschützgasse", "zug",
            "2026-10-01T08:00:00+02:00", "2026-10-01T09:00:00+02:00"),
        _wl("60: Fahrtbehinderung Schadhaftes Fahrzeug", "Fahrtbehinderung\nSchadhaftes Fahrzeug", "fahrzeug",
            "2026-10-01T08:21:00+02:00", "2026-10-01T09:00:00+02:00"),
    ]
    assert _shown(items) == [("60: Schadhafter Zug", "Betrieb ab Anschützgasse; Fahrtbehinderung.")]


def test_the_newest_long_message_stands_for_its_incident() -> None:
    # WL rewrote the long message an hour later; the first one was still valid.
    items = [
        _wl("D: Gleisbauarbeiten", "Linie D: Kein Betrieb zwischen Börse und Augasse. Grund: Gleisbauarbeiten.",
            "d-first", "2026-10-01T04:30:00+02:00", "2026-10-01T23:00:00+02:00"),
        _wl("D: Gleisbauarbeiten", "Linie D: Kein Betrieb zwischen Börse und Friedensbrücke. Grund: Gleisbauarbeiten.",
            "d-later", "2026-10-01T05:30:00+02:00", "2026-10-01T23:00:00+02:00"),
    ]
    (item,) = _built(items)
    assert (item["guid"], item["description"]) == (
        "d-later",
        "Kein Betrieb zwischen Börse und Friedensbrücke. Grund: Gleisbauarbeiten.",
    )


# 26E since 2026-09-25: a standing long message, and each night the stops of the replacement bus.
LONG_26E = _wl(
    "26E: Gleisbauarbeiten",
    "Die Kapazitäten der Ersatzlinie 26E sind nicht mit denen einer Straßenbahn vergleichbar. "
    "Weichen Sie daher nach Möglichkeit auf die U1 und die S-Bahn über Leopoldau S U oder Praterstern S U aus.",
    "dac4be80", "2026-09-25T08:55:00+02:00", "2026-12-12T23:55:00+01:00",
)
STOPS_26E = [
    _wl("26E: Busse halten Satzingerweg 41", "Busse halten\nSatzingerweg 41", "2008cbaf",
        "2026-10-01T00:00:14+02:00", "2026-10-01T23:59:59+02:00"),
    _wl("26E: Busse halten Bessemerstraße 1-3", "Busse halten\nBessemerstraße 1-3", "74ed68a2",
        "2026-10-01T00:00:14+02:00", "2026-10-01T23:59:59+02:00"),
    _wl("26E: Ersatzbus hält Karl-Waldbrunner-Platz vor Schloßhofer Straße",
        "Ersatzbus\nhält Karl-Waldbrunner-Platz vor Schloßhofer Straße", "cf73c659",
        "2026-10-01T00:00:14+02:00", "2026-10-01T23:59:59+02:00"),
]


def test_a_standing_long_message_keeps_the_stops_of_its_line() -> None:
    (item,) = _built([LONG_26E, *STOPS_26E])
    assert (item["guid"], item["title"]) == ("dac4be80", "26E: Gleisbauarbeiten")
    assert str(item["description"]).startswith(
        "Busse halten Satzingerweg 41, Bessemerstraße 1-3; "
        "Ersatzbus hält Karl-Waldbrunner-Platz vor Schloßhofer Straße. Die Kapazitäten der Ersatzlinie 26E"
    )


def test_the_stock_sentence_gives_way_in_a_combined_entry() -> None:
    items = [
        _wl("7A: Busse halten Laxenburger Straße 66", "Bauarbeiten\nBusse halten Laxenburger Straße 66", "35f178ee",
            "2026-10-01T05:10:13+02:00", "2026-10-02T00:45:00+02:00"),
        _wl("7A: Verkehrsüberlastung", "Linie 7A: Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen.",
            "d42feba3", "2026-10-01T15:50:00+02:00", "2026-10-01T23:55:00+02:00"),
    ]
    assert _shown(items) == [("7A: Verkehrsüberlastung, Bauarbeiten", "Bauarbeiten: Busse halten Laxenburger Straße 66.")]


def test_a_long_combined_entry_gives_up_its_last_consequences_first() -> None:
    # 62 on 2026-09-28: both incidents fit once the last consequence of the longer one goes.
    items = [
        _wl("62: Fahrtbehinderung Beschädigte Oberleitung", "Fahrtbehinderung\nBeschädigte Oberleitung", "o1",
            "2026-09-28T21:40:00+02:00", "2026-09-29T00:30:00+02:00"),
        _wl("62: Oberleitungsgebr Betrieb ab Hofwiesengasse", "Oberleitungsgebr\nBetrieb ab Hofwiesengasse", "o2",
            "2026-09-28T21:41:00+02:00", "2026-09-29T00:30:00+02:00"),
        _wl("62: Oberleitungsgebr Betrieb ab Eichenstraße", "Oberleitungsgebr\nBetrieb ab Eichenstraße", "o3",
            "2026-09-28T21:42:00+02:00", "2026-09-29T00:30:00+02:00"),
        _wl("62: ÖBB Bauarbeiten Betrieb ab Kliebergasse", "ÖBB Bauarbeiten\nBetrieb ab Kliebergasse", "b1",
            "2026-09-28T04:00:00+02:00", "2026-09-29T23:00:00+02:00"),
        _wl("62: Züge halten bei Linie 18, Richtung Burggasse", "Züge halten bei\nLinie 18, Richtung Burggasse", "b2",
            "2026-09-28T04:00:30+02:00", "2026-09-29T23:00:00+02:00"),
        _wl("62: ÖBB Bauarbeiten Kein Betrieb", "ÖBB Bauarbeiten\nKein Betrieb", "b3",
            "2026-09-28T04:01:26+02:00", "2026-09-29T23:00:00+02:00"),
    ]
    assert _shown(items) == [
        (
            "62: Oberleitungsgebrechen, ÖBB Bauarbeiten",
            # "; Kein Betrieb" would take the text to 183 characters.
            "Oberleitungsgebrechen: Fahrtbehinderung; Betrieb ab Hofwiesengasse, Eichenstraße. "
            "ÖBB Bauarbeiten: Betrieb ab Kliebergasse; Züge halten bei Linie 18, Richtung Burggasse.",
        )
    ]


def test_a_synonym_ticker_keeps_what_the_long_message_does_not_say() -> None:
    # 60 on 2026-09-22: the long message says "Schadhaftes Fahrzeug", the
    # display "Schadhafter Zug", the last ticker 19 minutes later.
    items = [
        _wl("60: Schadhaftes Fahrzeug",
            "Linie 60: Betrieb nur zwischen Westbahnhof S U und Hofwiesengasse. Weichen Sie ersatzweise auf die "
            "Linien 56A, 56B, 58B und 60A aus. Voraussichtliche Dauer: 10:00 Uhr. Grund: Schadhaftes Fahrzeug "
            "im Bereich Speisinger Straße 230.",
            "b98153df", "2026-09-22T09:38:00+02:00", "2026-09-22T23:55:00+02:00"),
        _wl("60: Schadhafter Zug Betrieb ab Hofwiesengasse", "Schadhafter Zug\nBetrieb ab Hofwiesengasse", "3ba4c649",
            "2026-09-22T09:42:33+02:00", "2026-09-22T10:55:00+02:00"),
        _wl("60: Schadhafter Zug Züge halten bei der Linie 62 Fahrtrichtung Lainz",
            "Schadhafter Zug\nZüge halten bei der Linie 62 Fahrtrichtung Lainz", "4cdc1f3d",
            "2026-09-22T09:57:05+02:00", "2026-09-22T10:56:00+02:00"),
    ]
    (item,) = _built(items)
    assert (item["guid"], item["title"]) == ("b98153df", "60: Schadhaftes Fahrzeug")
    # The long message says what riders have to do; the ticker joins its
    # measure's sentence, before the advice.
    assert str(item["description"]).startswith(
        "Betrieb nur zwischen Westbahnhof S U und Hofwiesengasse; "
        "Züge halten bei der Linie 62 Fahrtrichtung Lainz. Weichen Sie ersatzweise"
    )
    xml = bf._make_rss([item], _at("2026-09-22T10:01:00+02:00"), {}, lang="de")
    assert "Züge halten bei der Linie 62 Fahrtrichtung Lainz." in xml


def test_a_bare_hindrance_gives_way_to_the_long_messages_sentence() -> None:
    # 3A on 2026-09-17: the network change since midnight, a police operation at 14:12.
    items = [
        _wl("3A: 3A Netzänderung Betrieb ab Riemergasse", "3A Netzänderung\nBetrieb ab Riemergasse", "649036ab",
            "2026-09-16T00:00:07+02:00", "2026-09-17T23:59:59+02:00"),
        _wl("3A: Busse halten Kärntner Ring 5", "Busse halten\nKärntner Ring 5", "c535a3f2",
            "2026-09-16T00:00:07+02:00", "2026-09-17T23:59:59+02:00"),
        _wl("3A: Fahrtbehinderung wegen Polizeieinsatz",
            "Linie 3A: Betrieb nur zwischen Schottenring U und Stephansplatz U. Voraussichtliche Dauer: 14:45 Uhr. "
            "Grund: Polizeieinsatz im Bereich Wollzeile.",
            "612ada6e", "2026-09-17T14:12:46+02:00", "2026-09-17T23:55:00+02:00"),
    ]
    assert _shown(items) == [
        (
            "3A: Polizeieinsatz, Netzänderung",
            "Polizeieinsatz: Betrieb nur zwischen Schottenring U und Stephansplatz U. "
            "Netzänderung: Betrieb ab Riemergasse; Busse halten Kärntner Ring 5.",
        )
    ]


def test_the_display_names_what_the_stock_sentence_does_not() -> None:
    # D on 2026-09-28: a fire brigade operation beside the track works.
    items = [
        _wl("D: Feuerwehreinsatz Betrieb ab Schwarzenbergplatz",
            "Linie D: Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen.", "fire",
            "2026-09-28T18:20:00+02:00", "2026-09-28T23:55:00+02:00"),
        _wl("D: Betrieb ab Augasse", "Gleisbauarbeiten\nBetrieb ab Augasse", "works",
            "2026-09-28T04:00:07+02:00", "2026-09-30T23:59:59+02:00"),
    ]
    assert _shown(items) == [
        (
            "D: Feuerwehreinsatz, Gleisbauarbeiten",
            "Feuerwehreinsatz: Betrieb ab Schwarzenbergplatz. Gleisbauarbeiten: Betrieb ab Augasse.",
        )
    ]


def test_consequences_that_open_alike_share_the_opening() -> None:
    assert bf._shared_openings(
        ["Busse halten Bessemerstraße 1-3", "Busse halten auf Hauptfahrbahn", "Kein Betrieb", "Busse halten Hoßplatz 11"]
    ) == ["Busse halten Bessemerstraße 1-3, auf Hauptfahrbahn, Hoßplatz 11", "Kein Betrieb"]
    # A consequence with a comma of its own would read as one more stop.
    assert bf._shared_openings(
        ["Züge halten Donaufelder Straße 175-177", "Züge halten bei Li. 18 Richtung Burggasse, Stadthalle"]
    ) == ["Züge halten Donaufelder Straße 175-177", "Züge halten bei Li. 18 Richtung Burggasse, Stadthalle"]
