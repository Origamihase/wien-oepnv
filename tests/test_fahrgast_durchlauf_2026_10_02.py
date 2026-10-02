"""Passenger read-through of the live feed, 2026-10-02.

Every current item of ``docs/feed.xml`` and ``docs/feed.en.xml`` was read
the way a rider reads an info display. Five classes of defects came out of
it, each fixed where it arises rather than for the one item that showed it:

* Words glued together in the Stadt-Wien roadworks texts ("Derlinke
  Fahrstreifen", rank 6 of the German feed).
* An ``X`` the translation model put in front of a masked entity
  ("WipplingerstrX39; service fromXAugasse", rank 2 of the English feed).
* WL notice paragraphs and headings running into each other without a
  sentence end ("U1: Starke Nachfrage Die U1 wird …", "… Schwedenplatz U
  Haltestelle: Stammersdorf Von: …").
* A truncated summary ending on ". …" after a complete sentence.
* A ticker consequence shown without its full stop ("Busse halten bei
  Haltestelle N71").
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest

from src import build_feed
from src.feed_types import FeedItem
from src.utils.serialize import scrub_trojan_source_primitives
from src.utils.text import BLOCK_END_MARK, html_to_text, repair_glued_words

pytestmark = pytest.mark.usefixtures("time_line_today")


def _format(raw_title: str, raw_desc: str, *, category: str = "Hinweis") -> tuple[str, str]:
    item = cast(
        FeedItem,
        {
            "title": raw_title,
            "description": raw_desc,
            "source": "Wiener Linien",
            "category": category,
            "guid": "t",
            "link": "",
        },
    )
    formatted = build_feed._format_item_content(
        item,
        ident="t",
        starts_at=datetime(2026, 9, 30, 11, 0, tzinfo=UTC),
        ends_at=datetime(2027, 5, 31, 21, 0, tzinfo=UTC),
    )
    return formatted.title_out, formatted.desc_text_truncated


# ---------------- glued words ----------------


@pytest.mark.parametrize("separator", ["\x0b", "\x0c", "\x85", " ", " "])
def test_line_break_controls_become_a_space_in_the_cache(separator: str) -> None:
    scrubbed = scrub_trojan_source_primitives({"description": f"Der{separator}linke Fahrstreifen"})
    assert scrubbed == {"description": "Der linke Fahrstreifen"}


@pytest.mark.parametrize("separator", ["\x0b", " "])
def test_line_break_controls_become_a_space_in_the_feed(separator: str) -> None:
    assert build_feed._sanitize_text(f"Außerhalb{separator}der Arbeitszeit") == (
        "Außerhalb der Arbeitszeit"
    )


def test_invisible_primitives_are_still_removed() -> None:
    assert scrub_trojan_source_primitives("a‮b​c") == "abc"
    assert build_feed._sanitize_text("a‮b​c") == "abc"


@pytest.mark.parametrize(
    ("glued", "repaired"),
    [
        ("Derlinke Fahrstreifen", "Der linke Fahrstreifen"),
        ("Derrechte Fahrstreifen", "Der rechte Fahrstreifen"),
        ("Außerhalbder Arbeitszeit", "Außerhalb der Arbeitszeit"),
        ("Die Zufahrt zuden Objekten", "Die Zufahrt zu den Objekten"),
        ("Die Haltestelleder betroffenen", "Die Haltestelle der betroffenen"),
        ("Gleisschaden Umleitungab Volksoper", "Gleisschaden Umleitung ab Volksoper"),
    ],
)
def test_lower_case_glue_is_repaired(glued: str, repaired: str) -> None:
    assert repair_glued_words(glued) == repaired


@pytest.mark.parametrize(
    "word",
    ["zudem", "indem", "beiden", "derzeit", "Derzeit", "Dieselbe", "Desweiteren", "Stellungnahme"],
)
def test_real_words_stay_whole(word: str) -> None:
    assert repair_glued_words(word) == word


def test_roadworks_text_from_the_cache_reads_cleanly() -> None:
    _, desc = _format(
        "Rennweg 33A–37",
        "DieArbeiten erfolgen in der Zeit von 20:00 bis 05:00 Uhr. Derlinke Fahrstreifen "
        "in Fahrtrichtung stadteinwärts wird gesperrt.",
        category="Baustelle",
    )
    assert "Der linke Fahrstreifen" in desc
    assert "Derlinke" not in desc


# ---------------- EN: an X in front of a placeholder ----------------


def _placeholder(index: int) -> str:
    return build_feed._ENTITY_PLACEHOLDER_FORMAT.format(index=index)


def test_stray_x_before_a_placeholder_becomes_the_space() -> None:
    mapping = {_placeholder(0): "Wipplingerstr", _placeholder(1): "39", _placeholder(2): "Augasse"}
    model_output = (
        f"Trains keep in loop, {_placeholder(0)}X{_placeholder(1)}; "
        f"service fromX{_placeholder(2)}, Börse."
    )
    assert build_feed._unmask_entities(model_output, mapping) == (
        "Trains keep in loop, Wipplingerstr 39; service from Augasse, Börse."
    )


def test_stray_x_after_punctuation_is_dropped() -> None:
    mapping = {_placeholder(0): "Augasse"}
    assert build_feed._unmask_entities(f"(X{_placeholder(0)})", mapping) == "(Augasse)"


def test_adjacent_placeholders_stay_intact() -> None:
    mapping = {_placeholder(0): "Wipplingerstr", _placeholder(1): "39"}
    assert build_feed._unmask_entities(f"{_placeholder(0)} {_placeholder(1)}", mapping) == (
        "Wipplingerstr 39"
    )


def test_translation_cache_epoch_evicts_the_cached_debris() -> None:
    assert build_feed._TRANSLATION_CACHE_EPOCH >= 19


# ---------------- paragraph ends are sentence ends ----------------

_N31 = (
    '<p class="MsoNormal"><strong><u><span>Haltestellenauflassung der Linie N31 in Richtung '
    "Schwedenplatz U</span></u></strong></p> <p><strong><u><span>Haltestelle:</span></u>"
    "</strong><span> Stammersdorf&nbsp;</span></p> <p><span><strong><u><span>Von:</span></u>"
    "</strong><span> Br&uuml;nner Stra&szlig;e gegen&uuml;ber 262</span></span></p> "
    "<p><strong><u><span>Ersatzlos aufgelassen</span></u></strong></p> <p><strong><u><span>"
    "Dauer:</span></u></strong><span> Ab 30. September 2026, etwa 13:00 Uhr, bis voraussichtlich "
    "Mai 2027</span></p> <p><strong><u><span>Grund:</span></u></strong><span> "
    "Rohrleitungsarbeiten</span></p>"
)


def test_block_ends_are_marked_only_on_request() -> None:
    html = "<p>Eins</p><p>Zwei</p>"
    assert BLOCK_END_MARK not in html_to_text(html, collapse_newlines=True)
    assert html_to_text(html, collapse_newlines=True, mark_block_ends=True).count(
        BLOCK_END_MARK
    ) == 2


def test_stop_closure_reads_as_sentences() -> None:
    _, desc = _format("N31: Stammersdorf ersatzlos aufgelassen", _N31)
    assert desc.startswith(
        "Haltestellenauflassung der Linie N31 in Richtung Schwedenplatz U. "
        "Haltestelle: Stammersdorf. Von: Brünner Straße gegenüber 262. Ersatzlos aufgelassen."
    )
    # The dates are the time line's job.
    assert "Dauer:" not in desc
    assert BLOCK_END_MARK not in desc


def test_heading_that_repeats_the_title_is_dropped() -> None:
    _, desc = _format(
        "U1: Starke Nachfrage",
        "<p><strong>U1: Starke Nachfrage</strong></p> <p>Die U1 wird aufgrund der "
        "&Ouml;BB S-Bahn-Sperre zwischen Hauptbahnhof und Praterstern st&auml;rker "
        "nachgefragt als &uuml;blich.</p>",
    )
    assert desc.startswith("Die U1 wird aufgrund der ÖBB S-Bahn-Sperre")


def test_heading_in_other_words_than_the_title_is_dropped() -> None:
    _, desc = _format(
        "S80: Bauarbeiten",
        "<h2>Bauarbeiten S80</h2> <p>Wegen Bauarbeiten kommt es zu Teilausf&auml;llen "
        "der S80.</p>",
    )
    assert desc.startswith("Wegen Bauarbeiten kommt es zu Teilausfällen der S80.")


def test_heading_gets_its_full_stop_and_keeps_the_text_behind_it() -> None:
    _, desc = _format(
        "11/71: Gleisbauarbeiten (Phase 3)",
        "<h2>Gleisbauarbeiten Pantucekgasse - Fr&uuml;herer Betriebsschluss</h2> <p>Wegen "
        "Gleisbauarbeiten fahren die Linien 11 und 71 montags bis freitags abends nicht bis "
        "Kaiserebersdorf, Zinnergasse, sondern nur bis Zentralfriedhof 3. Tor.</p>",
    )
    assert desc.startswith("Pantucekgasse - Früherer Betriebsschluss. Wegen Gleisbauarbeiten")


def test_single_word_category_heading_is_left_to_the_category_strip() -> None:
    _, desc = _format(
        "N71: Umleitung",
        "<h2>Gleisbauarbeiten</h2> <p>Wegen Gleisbauarbeiten kommt es zu einer Umleitung "
        "der Linie N71.</p>",
    )
    assert desc.startswith("Wegen Gleisbauarbeiten kommt es zu einer Umleitung der Linie N71.")


def test_prose_paragraphs_keep_the_two_sentence_rule() -> None:
    _, desc = _format(
        "63A: Umleitung wegen Kranarbeiten",
        "<p>Wegen Kranarbeiten im Bereich Kerschensteinergasse zwischen Kundratstra&szlig;e "
        "und L&auml;ngenfeldgasse wird die Linie 63A umgeleitet.</p> <p><strong>Zeitraum:"
        "</strong><br />Von Montag, 05. Oktober 2026, auf Dauer der Arbeiten bis voraussichtlich "
        "Mitte November 2026.</p>",
    )
    assert desc.startswith(
        "Wegen Kranarbeiten im Bereich Kerschensteinergasse zwischen Kundratstraße und "
        "Längenfeldgasse wird die Linie 63A umgeleitet. ["
    )


# ---------------- truncation and ticker sentences ----------------


def test_cut_on_a_sentence_end_shows_no_ellipsis() -> None:
    text = ("x" * 140) + " Haltestelle: Wittelsbachstraße. Von: " + "Lange Straße " * 20
    assert build_feed._truncate_summary_180(text).endswith(" Haltestelle: Wittelsbachstraße.")


def test_cut_mid_sentence_keeps_the_ellipsis() -> None:
    assert build_feed._truncate_summary_180("Wort " * 60).endswith(" …")


def test_station_marker_is_not_a_sentence_end() -> None:
    assert not build_feed._SENTENCE_END_RE.search("Schwedenplatz U.")


def test_ticker_consequence_ends_with_a_full_stop() -> None:
    title, desc = _format(
        "N71: Ersatzverkehr Busse halten bei Haltestelle N71",
        "Ersatzverkehr\nBusse halten bei Haltestelle N71",
        category="Störung",
    )
    assert title == "N71: Ersatzverkehr"
    assert desc.startswith("Busse halten bei Haltestelle N71. [")
