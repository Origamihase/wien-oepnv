"""A WL ticker's title is its cause; the consequence goes into the description.

Operator request 2026-09-25, on the live item::

    T: 14A: Rettungseinsatz – Betrieb ab Laxenburger Straße / Gudrunstraße
    D: [Am 25.09.2026]

wanted as::

    T: 14A: Rettungseinsatz
    D: Betrieb ab Laxenburger Straße / Gudrunstraße [Am 25.09.2026]

and "a clever solution for further messages of this kind".
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from typing import cast

import pytest

import src.build_feed as bf
from src.feed_types import FeedItem

START = datetime(2026, 9, 25, 13, 59, tzinfo=UTC)
END = datetime(2026, 9, 25, 14, 58, tzinfo=UTC)


def _item(title: str, desc: str, *, source: str = "Wiener Linien", category: str = "Störung", guid: str = "t") -> FeedItem:
    return cast(
        FeedItem,
        {"title": title, "description": desc, "source": source, "category": category, "guid": guid, "link": ""},
    )


def _format(item: FeedItem, *, split_reason: bool = True) -> tuple[str, str]:
    formatted = bf._format_item_content(item, ident="t", starts_at=START, ends_at=END, split_reason=split_reason)
    return formatted.title_out, formatted.desc_text_truncated


# ---------------- the live item ----------------


def test_the_live_item_as_the_operator_asked() -> None:
    title, desc = _format(
        _item(
            "14A: Rettungseinsatz Betrieb ab Laxenburger Straße / Gudrunstraße",
            "Rettungseinsatz\nBetrieb ab Laxenburger Straße / Gudrunstraße",
        )
    )
    assert title == "14A: Rettungseinsatz"
    assert desc == "Betrieb ab Laxenburger Straße / Gudrunstraße [Am 25.09.2026]"


def test_the_default_is_the_published_behaviour() -> None:
    item = _item("14A: Rettungseinsatz Betrieb ab Gudrunstraße", "Rettungseinsatz\nBetrieb ab Gudrunstraße")
    formatted = bf._format_item_content(item, ident="t", starts_at=START, ends_at=END)
    assert formatted.title_out == "14A: Rettungseinsatz"


# ---------------- which titles split ----------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        # a cause from the word list
        ("14A: Rettungseinsatz Betrieb ab Gudrunstraße", ("14A: Rettungseinsatz", "Betrieb ab Gudrunstraße")),
        ("1A: Veranstaltung Kein Betrieb", ("1A: Veranstaltung", "Kein Betrieb")),
        ("12: Oberleitungsgebr Betrieb ab Lange Gasse", ("12: Oberleitungsgebrechen", "Betrieb ab Lange Gasse")),
        ("31: Demonstration – Betrieb ab Wallensteinstraße", ("31: Demonstration", "Betrieb ab Wallensteinstraße")),
        # a cause the word list does not know, found by its consequence
        ("O: Schadhafter Zug Betrieb ab Quartier Belvedere", ("O: Schadhafter Zug", "Betrieb ab Quartier Belvedere")),
        ("5: Stromstörung Betrieb ab Rosensteingasse", ("5: Stromstörung", "Betrieb ab Rosensteingasse")),
        ("6: PKW im Gleis Betrieb ab Raxstraße", ("6: PKW im Gleis", "Betrieb ab Raxstraße")),
        (
            "62: Schadhafter Zug Züge halten bei der Linie 62 Fahrtrichtung Lainz",
            ("62: Schadhafter Zug", "Züge halten bei der Linie 62 Fahrtrichtung Lainz"),
        ),
        ("71E: Ersatzverkehr Busse halten Simmeringer Hauptstraße", ("71E: Ersatzverkehr", "Busse halten Simmeringer Hauptstraße")),
    ],
)
def test_cause_and_consequence(title: str, expected: tuple[str, str]) -> None:
    assert bf._reason_and_fragment(title) == expected


@pytest.mark.parametrize(
    "title",
    [
        "12A: Betrieb ab Johnstraße U",  # no cause in front
        "25/26: Linien 25 und 26 Betrieb ab Josef-Baumann-Gasse",  # digits: not a cause
        "3A: 3A Netzänderung Betrieb ab Riemergasse",
        "U6: Rettungseinsatz im Bereich Längenfeldgasse Betrieb ab Meidling",  # four words
        "16A: Rettungseinsatz",
        "1/2/2A: Demonstration am 19.09.2026",
        "O: Züge halten bei Linie O",
    ],
)
def test_titles_that_do_not_split(title: str) -> None:
    assert bf._reason_and_fragment(title) is None


def test_a_notice_keeps_its_place_in_the_title() -> None:
    title, _ = _format(
        _item("D: Gleisbauarbeiten Althanstraße", "Wegen Gleisbauarbeiten in der Althanstraße …", category="Hinweis")
    )
    assert title == "D: Gleisbauarbeiten – Althanstraße"


def test_only_wiener_linien_tickers() -> None:
    item = _item("REX 1: Schadhafter Zug Betrieb ab Wien Meidling", "x", source="ÖBB")
    title, _ = _format(item)
    assert title == "REX 1: Schadhafter Zug Betrieb ab Wien Meidling"


# ---------------- what the description becomes ----------------


def test_a_summary_with_its_own_content_follows_the_consequence_in_whole_sentences() -> None:
    title, desc = _format(
        _item(
            "13A: Veranstaltung Busse halten bei der Linie 14A",
            "Betrieb nur zwischen Hauptbahnhof S U und Neubaugasse U. Weichen Sie ersatzweise auf die Linien "
            "U3, 5, 12, 46, 52, 48A aus. Voraussichtliche Dauer: bis 22:00 Uhr. Grund: Veranstaltung im "
            "Bereich Neubaugasse.",
        )
    )
    assert title == "13A: Veranstaltung"
    body = desc.removesuffix(" [Am 25.09.2026]")
    assert body.startswith("Busse halten bei der Linie 14A. Betrieb nur zwischen Hauptbahnhof")
    # Cut behind a sentence, not in the middle of the next one.
    assert body.endswith("48A aus."), body
    assert len(body) <= 180


def test_a_summary_that_already_names_the_consequence_is_left_alone() -> None:
    _, desc = _format(
        _item("31: Demonstration Betrieb ab Ring", "Wegen einer Demonstration: Betrieb ab Ring bis 18 Uhr.")
    )
    assert desc.startswith("Wegen einer Demonstration: Betrieb ab Ring bis 18 Uhr.")
    assert desc.count("Betrieb ab Ring") == 1


def test_a_summary_that_repeats_cause_and_consequence_gives_way() -> None:
    title, desc = _format(_item("O: Schadhafter Zug Betrieb ab Quartier Belvedere", "Schadhafter Zug\nBetrieb ab Quartier Belvedere"))
    assert (title, desc) == ("O: Schadhafter Zug", "Betrieb ab Quartier Belvedere [Am 25.09.2026]")


def test_a_summary_with_part_of_the_consequence_gives_way() -> None:
    # Not the title verbatim, so the duplicate check keeps it; with the
    # cause in the title, "Kein Betrieb" says nothing the consequence does not.
    title, desc = _format(_item("13A: Betriebsstörung Kein Betrieb ab 10 Uhr", "Betriebsstörung\nKein Betrieb"))
    assert (title, desc) == ("13A: Betriebsstörung", "Kein Betrieb ab 10 Uhr [Am 25.09.2026]")


@pytest.mark.parametrize(
    ("text", "limit", "cut_after"),
    [
        ("Weichen Sie auf die Linien U3, 48A aus. Voraussichtliche Dauer: bis 22:00 Uhr.", 60, "aus."),
        ("Voraussichtliche Dauer: bis 22:00 Uhr. Grund: Veranstaltung im Bereich Neubaugasse.", 60, "Uhr."),
    ],
)
def test_the_last_sentence_end(text: str, limit: int, cut_after: str) -> None:
    end = bf._last_sentence_end(text, limit)
    assert end is not None and text[:end].endswith(cut_after)


@pytest.mark.parametrize(
    "text",
    [
        "Fahrt bis Wien Hbf. Richtung Süden",
        "Ab 17. Februar gesperrt",
        "Gerasdorf b. Wien und Karlsplatz U. Bereich",
        "Bahnhst bzw. Gerasdorf",
        "Simmeringer Hauptstraße ggü. Nummer 197",
    ],
)
def test_abbreviations_and_numbers_are_no_sentence_end(text: str) -> None:
    assert bf._last_sentence_end(text, len(text)) is None


# ---------------- no two visible titles alike ----------------


def _render_titles(items: list[FeedItem], monkeypatch: pytest.MonkeyPatch, max_items: int = 10) -> list[str]:
    monkeypatch.setattr(bf.feed_config, "MAX_ITEMS", max_items)
    for index, it in enumerate(items):
        it["guid"] = f"g{index}"
        it["starts_at"] = START
        it["ends_at"] = END
    xml = bf._make_rss(items, START, {}, lang="de")
    xml = re.sub(r"<!\[CDATA\[(.*?)\]\]>", lambda m: m.group(1), xml, flags=re.S)
    channel = ET.fromstring(xml.encode("utf-8")).find("channel")
    assert channel is not None
    return [str(item.findtext("title")) for item in channel.findall("item")]


def test_tickers_of_one_incident_keep_their_long_titles(monkeypatch: pytest.MonkeyPatch) -> None:
    titles = _render_titles(
        [
            _item("49: Gleisschaden", "Unregelmäßige Intervalle."),
            _item("49: Gleisschaden Betrieb ab Urban-Loritz-Platz", "x"),
            _item("49: Gleisschaden Betrieb ab Hütteldorfer Straße", "x"),
            _item("14A: Rettungseinsatz Betrieb ab Gudrunstraße", "x"),
        ],
        monkeypatch,
    )
    assert titles == [
        "49: Gleisschaden",
        "49: Gleisschaden – Betrieb ab Urban-Loritz-Platz",
        "49: Gleisschaden – Betrieb ab Hütteldorfer Straße",
        "14A: Rettungseinsatz",
    ]
    assert len(set(titles)) == len(titles)


def test_the_same_cause_on_different_lines_is_no_collision(monkeypatch: pytest.MonkeyPatch) -> None:
    titles = _render_titles(
        [
            _item("14A: Rettungseinsatz Betrieb ab Gudrunstraße", "x"),
            _item("16A: Rettungseinsatz Betrieb ab Alaudagasse", "x"),
        ],
        monkeypatch,
    )
    assert titles == ["14A: Rettungseinsatz", "16A: Rettungseinsatz"]


def test_only_visible_items_count(monkeypatch: pytest.MonkeyPatch) -> None:
    titles = _render_titles(
        [
            _item("49: Gleisschaden Betrieb ab Urban-Loritz-Platz", "x"),
            _item("49: Gleisschaden Betrieb ab Hütteldorfer Straße", "x"),
        ],
        monkeypatch,
        max_items=1,
    )
    assert titles == ["49: Gleisschaden"]


def test_collisions_by_index() -> None:
    items = [
        _item("49: Gleisschaden", "x"),
        _item("49: Gleisschaden Betrieb ab Urban-Loritz-Platz", "x"),
        _item("49: Gleisschaden Betrieb ab Hütteldorfer Straße", "x"),
        _item("D: Gleisbauarbeiten Althanstraße", "x", category="Hinweis"),
        _item("D: Gleisbauarbeiten Währinger Straße", "x", category="Hinweis"),
    ]
    assert bf._short_title_collisions(items) == {1, 2}
