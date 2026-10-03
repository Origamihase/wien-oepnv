"""Independent re-check of the 2026-10-02 changes, 2026-10-03.

Re-rendering 2 126 distinct notices from the cache history (WL, ÖBB, Stadt
Wien) with the merged code left three shapes of upstream damage standing:

* ``St.Pölten`` — ÖBB writes the abbreviation of "Sankt" glued to the name,
  so a notice read "St. Pölten Hauptbahnhof" in its title and "St.Pölten
  Hbf" in its text.
* A space in front of a comma or full stop in WL tickers
  ("Betrieb ab Enkplatz , Grillgasse", "Voraussichtliche Dauer: 12:15 Uhr
  ."). In one ticker the space also made the title's consequence differ
  from the text's, so the same sentence stood twice.
* A place clause without a place ("Grund: Verkehrsunfall im Bereich .").
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest

from src import build_feed
from src.feed_types import FeedItem
from src.utils.text import repair_glued_words, repair_saint_abbreviation

pytestmark = pytest.mark.usefixtures("time_line_today")


def _format(title: str, desc: str, *, source: str = "Wiener Linien") -> tuple[str, str]:
    item = cast(
        FeedItem,
        {
            "title": title,
            "description": desc,
            "source": source,
            "category": "Störung",
            "guid": "t",
            "link": "",
        },
    )
    formatted = build_feed._format_item_content(
        item,
        ident="t",
        starts_at=datetime(2026, 10, 3, 6, 0, tzinfo=UTC),
        ends_at=datetime(2026, 10, 4, 6, 0, tzinfo=UTC),
    )
    return formatted.title_out, formatted.desc_text_truncated


@pytest.mark.parametrize(
    ("glued", "spaced"),
    [
        ("St.Pölten Hbf", "St. Pölten Hbf"),
        ("St.Andrä-Wördern Bahnhof", "St. Andrä-Wördern Bahnhof"),
        ("in Hinterstoder Bahnhof [in St.Pankraz]", "in Hinterstoder Bahnhof [in St. Pankraz]"),
        ("St. Pölten Hbf", "St. Pölten Hbf"),
    ],
)
def test_saint_abbreviation_gets_its_space(glued: str, spaced: str) -> None:
    assert repair_saint_abbreviation(glued) == spaced
    assert repair_glued_words(glued) == spaced


@pytest.mark.parametrize("text", ["Hbf.Nord", "Last.Ende", "Nast.Ende", "Fest.Veranstaltung"])
def test_only_the_saint_abbreviation_is_touched(text: str) -> None:
    assert repair_saint_abbreviation(text) == text


def test_oebb_title_and_text_both_say_st_poelten() -> None:
    title, desc = _format(
        "S40: Wien Franz-Josefs-Bahnhof ↔ St.Andrä-Wördern",
        "Wegen Bauarbeiten können zwischen Wien Franz-Josefs-Bahnhof und "
        "St.Andrä-Wördern Bahnhof keine R 40-Züge fahren.",
        source="ÖBB",
    )
    assert "St.A" not in title
    assert "St.A" not in desc
    assert "St. Andrä-Wördern" in desc


def test_space_before_comma_is_gone_and_the_sentence_is_not_doubled() -> None:
    _, desc = _format("2: Veranstaltung Betrieb ab Ring , Volkstheater U", "Betrieb ab Ring, Volkstheater U")
    assert desc.count("Volkstheater") == 1
    assert " ," not in desc


def test_space_before_full_stop_is_gone() -> None:
    _, desc = _format(
        "9A: Falschparker",
        "Linie 9A: Fahrtbehinderung in Richtung Meidling Hauptstraße U. "
        "Voraussichtliche Dauer: 12:15 Uhr . Grund: Falschparker im Bereich Ratschkygasse.",
    )
    assert "Uhr ." not in desc
    assert "12:15 Uhr." in desc


@pytest.mark.parametrize("place", ["Bereich", "Haltestellenbereich"])
def test_place_clause_without_a_place_goes(place: str) -> None:
    _, desc = _format(
        "31A: Verkehrsunfall",
        f"Linie 31A: Unregelmäßige Intervalle in beiden Richtungen. Grund: Verkehrsunfall im {place} .",
    )
    assert "Grund: Verkehrsunfall." in desc
    assert "Bereich" not in desc


def test_place_clause_with_a_place_stays() -> None:
    _, desc = _format(
        "32A: Rettungseinsatz",
        "Linie 32A: Unregelmäßige Intervalle in beiden Richtungen. "
        "Grund: Rettungseinsatz im Bereich Gerasdorfer Straße .",
    )
    assert "im Bereich Gerasdorfer Straße." in desc


def test_ellipsis_keeps_its_dots() -> None:
    _, desc = _format("X: Y", "Es kommt zu Verzögerungen ...")
    assert "..." in desc or "…" in desc
