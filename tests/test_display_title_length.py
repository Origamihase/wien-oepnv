"""Titles beyond the display length are shortened at their own seams.

The German feed runs on Full-HD info displays read from a distance. On
2026-10-02 it carried a WL notice whose title was two whole sentences
(137 characters) and two Stadt Wien Baustellen with a long "von … bis …"
section (97 and 85). See ``_DISPLAY_TITLE_TARGET`` in ``src/build_feed.py``.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from src.build_feed import (
    _DISPLAY_TITLE_TARGET,
    _format_item_content,
    _compact_baustellen_section,
    _display_title,
    _shorten_wl_sentence_title,
)
from src.feed_types import FeedItem

_STADIONBRUECKE = (
    "Haltestelle Stadionbrücke im Rahmen des Straßenbahn-Neubaus der Linie 18 aufgelassen. "
    "Bitte auf nahegelegene Haltestellen ausweichen."
)
_PARLAMENT = (
    "Haltestelle Parlament zur Beschleunigung der Straßenbahnlinien dauerhaft aufgelassen. "
    "Bitte auf nahegelegene Haltestellen ausweichen."
)


def test_a_sentence_title_keeps_what_it_reports() -> None:
    item: FeedItem = {
        "source": "Wiener Linien",
        "category": "Störung",
        "title": f"18: {_STADIONBRUECKE}",
        "description": _STADIONBRUECKE,
    }
    assert _display_title(item) == "18: Haltestelle Stadionbrücke aufgelassen"
    # The item itself keeps its title: dedupe and merges compare it.
    assert item["title"] == f"18: {_STADIONBRUECKE}"


def test_the_rendered_item_shows_the_whole_text_below_the_short_title() -> None:
    # Before, title and description were the same text and the duplicate
    # check left only the date underneath.
    vienna = ZoneInfo("Europe/Vienna")
    item: FeedItem = {
        "source": "Wiener Linien",
        "category": "Störung",
        "title": f"18: {_STADIONBRUECKE}",
        "description": _STADIONBRUECKE,
        "guid": "7aa6585f",
        "link": "https://www.wienerlinien.at/ogd_realtime",
    }
    formatted = _format_item_content(
        item, "7aa6585f", datetime(2026, 7, 13, 14, 7, tzinfo=vienna), datetime(2026, 12, 31, 14, 7, tzinfo=vienna)
    )
    assert formatted.title_out == "18: Haltestelle Stadionbrücke aufgelassen"
    assert "im Rahmen des Straßenbahn-Neubaus der Linie 18" in formatted.desc_text_truncated
    assert "Bitte auf nahegelegene Haltestellen ausweichen." in formatted.desc_text_truncated


def test_an_adverb_in_front_of_the_participle_stays() -> None:
    assert _shorten_wl_sentence_title(_PARLAMENT, _PARLAMENT) == "Haltestelle Parlament dauerhaft aufgelassen"


def test_a_short_first_sentence_keeps_its_reason() -> None:
    text = "Haltestelle Oper wegen Demo aufgelassen. Bitte ausweichen."
    assert _shorten_wl_sentence_title(f"2: {text}", text) == "2: Haltestelle Oper wegen Demo aufgelassen"


def test_nothing_leaves_the_title_that_the_description_does_not_say() -> None:
    title = f"18: {_STADIONBRUECKE}"
    assert _shorten_wl_sentence_title(title, "Haltestelle Stadionbrücke aufgelassen.") == title


def test_abbreviations_are_no_sentence_end() -> None:
    for title in (
        "1: Bhf. Hütteldorf ÖBB-Ersatzbus für 80",
        "71: Ersatzverkehr Busse halten Simmeringer Hauptstraße ggü. 197",
    ):
        assert _shorten_wl_sentence_title(title, title) == title


def test_other_sources_keep_their_title() -> None:
    long_route = "R 40/REX 41/REX 4/S 40: Wien Franz-Josefs-Bahnhof ↔ St.Andrä-Wördern / Tulln an der Donau"
    item: FeedItem = {"source": "ÖBB", "title": long_route, "description": long_route}
    assert _display_title(item) == long_route


def test_a_long_section_drops_crossing_and_second_names() -> None:
    assert _compact_baustellen_section(
        "U2: Rechte Wienzeile von Kreuzung Ramperstorffergasse bis Kreuzung Pilgramgasse und Pilgrambrücke"
    ) == "U2: Rechte Wienzeile von Ramperstorffergasse bis Pilgramgasse"
    assert _compact_baustellen_section(
        "U4: Vordere Zollamtsstraße von Marxergasse und Kleine Marxerbrücke bis Radetzkybrücke"
    ) == "U4: Vordere Zollamtsstraße von Marxergasse bis Radetzkybrücke"


def test_a_section_stops_shortening_once_it_fits() -> None:
    title = "U2: Wienzeile von Kreuzung Ramperstorffergasse bis Kreuzung Pilgramgasse und Brücke"
    out = _compact_baustellen_section(title)
    assert out == "U2: Wienzeile von Ramperstorffergasse bis Pilgramgasse und Brücke"
    assert len(out) <= _DISPLAY_TITLE_TARGET


def test_a_short_section_is_left_alone() -> None:
    title = "Bacherplatz von Kreuzung Arbeitergasse bis Gasse und Spengergasse"
    assert len(title) <= _DISPLAY_TITLE_TARGET
    assert _compact_baustellen_section(title) == title


def test_a_baustellen_title_is_shown_compact() -> None:
    item: FeedItem = {
        "source": "Stadt Wien – Baustellen",
        "title": "U2: Rechte Wienzeile von Kreuzung Ramperstorffergasse bis Kreuzung Pilgramgasse und Pilgrambrücke",
        "description": "Für den Neubau der U-Bahnstation der U2 Pilgramgasse wird die Rechte Wienzeile gesperrt.",
    }
    assert _display_title(item) == "U2: Rechte Wienzeile von Ramperstorffergasse bis Pilgramgasse"
