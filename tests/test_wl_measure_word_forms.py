"""A measure named as a noun and as a participle is one measure (2026-11-06).

Live on 2026-11-06 at 05:30 Vienna time "66A: Feuerwehreinsatz" read "Die
Linie 66A wird … über Inzersdorfer Straße umgeleitet; Umleitung über
Inzersdorfer Straße." (check of the KI watcher's report): the long message
and its display ticker named the detour once each, and ``_said_already``
knew "umgeleitet" and "Umleitung" as two words. Now a noun in -ung and a
participle of one verb share a stem (``_word_stem``), the word formation and
not a list. An inflected form is at most three letters longer than its word
(``_INFLECTION_LETTERS``): a compound is another word, "Betriebsschluss"
does not say "Betrieb".

Mutations checked against this file (each one caught, by the test named):

* the stem rule is dropped → ``test_the_detour_is_named_once``, ``test_a_noun_and_its_participle_share_a_stem``.
* a stem is taken from any word → ``test_other_words_have_no_stem``.
* the prefix rule reads a compound as an inflection → ``test_a_compound_is_another_word``.
* a stem match ignores the rest of the message → ``test_another_detour_is_news``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest

import src.build_feed as bf
from src.feed.merge import deduplicate_fuzzy
from src.feed_build.wl_ticker_text import _said_already, _word_stem
from src.feed_build.wl_tickers import _message_tokens
from src.feed_types import FeedItem

LONG_66A = (
    "Die Linie 66A wird in Richtung Reumannplatz U zwischen Laxenburger Straße / Troststraße und Reumannplatz U "
    "über Inzersdorfer Straße umgeleitet. Voraussichtliche Dauer: 06:00 Uhr. "
    "Grund: Feuerwehreinsatz im Bereich Troststraße # Ettenreichgasse."
)


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(UTC)


def _wl(title: str, description: str, guid: str, start: str, end: str | None, *, wl_id: str | None = None) -> FeedItem:
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


def _shown(items: list[FeedItem]) -> list[tuple[str, str]]:
    read = cast(list[FeedItem], bf._normalize_item_datetimes(bf._post_filter_wl(list(items))))
    fuzzy = deduplicate_fuzzy(cast(list[dict[str, Any]], bf._dedupe_items(read)))
    return [(str(it["title"]), str(it["description"])) for it in bf._merge_wl_ticker_clusters(cast(list[FeedItem], fuzzy))]


@pytest.mark.parametrize(
    ("noun", "participle"),
    [
        ("umleitung", "umgeleitet"),
        ("umleitungen", "umgeleiteten"),
        ("kurzführung", "kurzgeführt"),
        ("verlegung", "verlegt"),
        ("auflassung", "aufgelassen"),
        ("auflassung", "aufgelassene"),
        ("einstellung", "eingestellt"),
        ("verspätung", "verspätet"),
        ("einschränkung", "eingeschränkt"),
        ("sperrung", "gesperrt"),
    ],
)
def test_a_noun_and_its_participle_share_a_stem(noun: str, participle: str) -> None:
    assert _word_stem(noun) is not None
    assert _word_stem(noun) == _word_stem(participle)


@pytest.mark.parametrize("word", ["betrieb", "betriebsschluss", "haltestelle", "halten", "züge", "gersthof", "gegen", "bereits"])
def test_other_words_have_no_stem(word: str) -> None:
    assert _word_stem(word) is None


def test_the_participle_of_another_verb_is_another_word() -> None:
    assert _word_stem("richtung") != _word_stem("eingerichtet")


def test_the_detour_is_named_once() -> None:
    said = _message_tokens(f"66A: Feuerwehreinsatz {LONG_66A}")
    assert _said_already("Umleitung über Inzersdorfer Straße", said)


def test_another_detour_is_news() -> None:
    said = _message_tokens(f"66A: Feuerwehreinsatz {LONG_66A}")
    assert not _said_already("Umleitung über Gersthof", said)


def test_a_compound_is_another_word() -> None:
    # "Betriebsschluss" in a long message's "Voraussichtliche Dauer: Bis
    # Betriebsschluss." (97A on 2026-11-04) is no "Betrieb".
    said = _message_tokens("Unregelmäßige Intervalle in beiden Richtungen. Voraussichtliche Dauer: Bis Betriebsschluss.")
    assert not _said_already("Betrieb ab Löschniggasse", said)
    assert _said_already("Verspätung", _message_tokens("Verspätungen in beiden Richtungen"))


def test_the_long_message_stands_without_its_ticker_s_detour() -> None:
    # The cache of 2026-11-06 05:31: the long message at 05:05, the display ticker at 05:10.
    long_message = _wl("66A: Feuerwehreinsatz", LONG_66A, "8ea54fca", "2026-11-06T05:05:00+01:00", None, wl_id="I20261106-0002")
    ticker = _wl(
        "66A: Feuerwehreinsatz Umleitung über Inzersdorfer Straße",
        "Feuerwehreinsatz\nUmleitung über Inzersdorfer Straße",
        "08003932", "2026-11-06T05:10:01+01:00", "2026-11-06T06:09:00+01:00",
    )
    assert _shown([long_message, ticker]) == [("66A: Feuerwehreinsatz", LONG_66A)]
