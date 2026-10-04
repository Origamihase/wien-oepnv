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
    _mark_house_numbers,
    _section_collisions,
    _compact_baustellen_section,
    _compact_line_prefix,
    _display_title,
    _display_title_parts,
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
        "link": "https://www.wienerlinien.at/ogd_realtime",
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
    item: FeedItem = {"source": "ÖBB", "title": long_route, "description": long_route, "link": ""}
    # Not shortened; only the line list loses its spaces.
    assert _display_title(item) == long_route.replace("R 40/REX 41/REX 4/S 40", "R40/REX41/REX4/S40")


def test_a_long_section_drops_crossing_and_second_names() -> None:
    assert _compact_baustellen_section(
        "U2: Rechte Wienzeile von Kreuzung Ramperstorffergasse bis Kreuzung Pilgramgasse und Pilgrambrücke"
    ) == "U2: Rechte Wienzeile von Ramperstorffergasse bis Pilgramgasse"
    assert _compact_baustellen_section(
        "U4: Vordere Zollamtsstraße von Marxergasse und Kleine Marxerbrücke bis Radetzkybrücke"
    ) == "U4: Vordere Zollamtsstraße von Marxergasse bis Radetzkybrücke"


def test_a_section_stops_shortening_once_it_fits() -> None:
    title = "U2: Wienzeile von Kreuzung Kaigasse bis Kreuzung Bachgasse und Steg"
    out = _compact_baustellen_section(title)
    assert out == "U2: Wienzeile von Kaigasse bis Bachgasse und Steg"
    assert len(out) <= _DISPLAY_TITLE_TARGET


def test_a_short_section_is_left_alone() -> None:
    title = "Platz von Kreuzung Hofgasse bis Gasse und Weg"
    assert len(title) <= _DISPLAY_TITLE_TARGET
    assert _compact_baustellen_section(title) == title


_LANDSTRASSER: FeedItem = {
    "source": "Stadt Wien – Baustellen",
    "title": "Landstraßer Hauptstraße von Emmerich-Teuber-Platz und Juchgasse bis Schlachthausgasse",
    "description": "Es wird lediglich die bestehende Bushaltestelle bei der Rabengasse örtlich verlegt.",
    "link": "",
}


def test_a_long_section_moves_into_the_description() -> None:
    # Target 50, operator request 2026-10-02 ("70 Zeichen … sollte echt ne Ausnahme sein").
    assert _display_title_parts(_LANDSTRASSER) == (
        "Landstraßer Hauptstraße",
        "Von Emmerich-Teuber-Platz und Juchgasse bis Schlachthausgasse",
    )
    item: FeedItem = {
        "source": "Stadt Wien – Baustellen",
        "title": "Universitätsstraße Kreuzung Landesgerichtsstraße und Garnisongasse",
        "description": "",
        "link": "",
    }
    assert _display_title_parts(item) == ("Universitätsstraße", "Kreuzung Landesgerichtsstraße und Garnisongasse")
    # "Kreuzung" in front of an end goes in the lead too.
    item = {
        "source": "Stadt Wien – Baustellen",
        "title": "U2: Rechte Wienzeile von Kreuzung Ramperstorffergasse bis Kreuzung Pilgramgasse und Pilgrambrücke",
        "description": "",
        "link": "",
    }
    assert _display_title_parts(item) == (
        "U2: Rechte Wienzeile",
        "Von Ramperstorffergasse bis Pilgramgasse und Pilgrambrücke",
    )


def test_the_section_opens_the_rendered_description() -> None:
    vienna = ZoneInfo("Europe/Vienna")
    formatted = _format_item_content(
        _LANDSTRASSER, "landstrasser", datetime(2026, 3, 9, tzinfo=vienna), datetime(2027, 8, 31, tzinfo=vienna)
    )
    assert formatted.title_out == "Landstraßer Hauptstraße"
    assert formatted.desc_text_truncated.startswith(
        "Von Emmerich-Teuber-Platz und Juchgasse bis Schlachthausgasse: Es wird lediglich die bestehende Bushaltestelle"
    )


def test_two_sites_on_one_street_keep_their_sections() -> None:
    other: FeedItem = dict(_LANDSTRASSER)  # type: ignore[assignment]
    other["title"] = "Landstraßer Hauptstraße von Rochusgasse und Weyrgasse bis Kardinal-Nagl-Platz"
    assert _section_collisions([_LANDSTRASSER, other]) == {0, 1}
    assert _section_collisions([_LANDSTRASSER]) == set()
    assert _display_title(_LANDSTRASSER, drop_section=False) == (
        "Landstraßer Hauptstraße von Emmerich-Teuber-Platz bis Schlachthausgasse"
    )


def test_a_spelled_out_start_leaves_a_wl_title() -> None:
    # The time line says "[Ab 07.04.2026]" already.
    item: FeedItem = {
        "source": "Wiener Linien",
        "title": "56A/56B/58A/58B: Bauarbeiten Maxingstraße ab 07. April 2026",
        "description": "Bauarbeiten",
        "link": "",
        "starts_at": datetime(2026, 4, 7, tzinfo=ZoneInfo("Europe/Vienna")),
    }
    assert _display_title(item) == "56A/56B/58A/58B: Bauarbeiten Maxingstraße"
    del item["starts_at"]
    assert _display_title(item) == "56A/56B/58A/58B: Bauarbeiten Maxingstraße ab 07. April 2026"


def test_an_oebb_line_list_is_written_without_spaces() -> None:
    # Operator request 2026-10-02: "R40/REX41/REX4/S40" like U6 or S1/S2.
    item: FeedItem = {
        "source": "ÖBB",
        "title": "R 40/REX 41/REX 4/S 40: Wien Franz-Josefs-Bahnhof ↔ Tulln an der Donau",
        "description": "Keine R 40-Züge.",
        "link": "",
    }
    assert _display_title(item) == "R40/REX41/REX4/S40: Wien Franz-Josefs-Bahnhof ↔ Tulln an der Donau"
    assert _compact_line_prefix("S 45: Wien Hütteldorf ↔ Wien Handelskai") == "S45: Wien Hütteldorf ↔ Wien Handelskai"
    assert _compact_line_prefix("Aufhebung Verkehrseinschränkung: S 45: Wien Handelskai") == (
        "Aufhebung Verkehrseinschränkung: S45: Wien Handelskai"
    )
    # A route without a line, and the station names, stay as they are.
    assert _compact_line_prefix("Wien Hauptbahnhof ↔ Wien Westbahnhof") == "Wien Hauptbahnhof ↔ Wien Westbahnhof"


def test_house_numbers_read_as_an_address() -> None:
    # "Rennweg von 33A bis 37" read as the bus 33A and the tram 37 (2026-10-02).
    item: FeedItem = {
        "source": "Stadt Wien – Baustellen", "title": "Rennweg von 33A bis 37", "description": "", "link": ""
    }
    assert _display_title(item) == "Rennweg 33A–37"
    assert _mark_house_numbers("U2/U5: Kirchengasse 1 bis 30") == "U2/U5: Kirchengasse 1–30"
    assert _mark_house_numbers("Rasumofskygasse von 1 bis 2") == "Rasumofskygasse 1–2"
    assert _mark_house_numbers("Siebenbrunnengasse von Siebenbrunnenplatz bis 44") == (
        "Siebenbrunnengasse von Siebenbrunnenplatz bis Nr. 44"
    )


def test_streets_and_number_spans_of_their_own_stay() -> None:
    for title in (
        "Matzleinsdorfer Platz 3-4 bis 5",
        "Neilreichgasse von Gudrunstraße bis Davidgasse",
        "Schottenring 11",
    ):
        assert _mark_house_numbers(title) == title


# --- the target itself: 50 characters (operator decision 2026-10-02) -------
#
# The tests above measure against ``_DISPLAY_TITLE_TARGET`` and passed with
# any value of it: set to 60, the whole suite stayed green except one
# Baustellen description (test-suite audit 2026-10-04). These pin the
# decided length at its edge, 50 characters kept as they are, 51 shortened.


def _baustelle(title: str) -> FeedItem:
    return {"source": "Stadt Wien – Baustellen", "title": title, "description": "Es wird gearbeitet.", "link": ""}


def test_a_wl_sentence_title_of_50_characters_keeps_its_reason() -> None:
    text = "Haltestelle Kagran wegen einer Demo aufgelassen. Bitte ausweichen."
    out = _shorten_wl_sentence_title(f"2: {text}", text)
    assert out == "2: Haltestelle Kagran wegen einer Demo aufgelassen"
    assert len(out) == 50


def test_a_wl_sentence_title_of_51_characters_loses_its_reason() -> None:
    text = "Haltestelle Rathaus wegen einer Demo aufgelassen. Bitte ausweichen."
    assert len("2: Haltestelle Rathaus wegen einer Demo aufgelassen") == 51
    assert _shorten_wl_sentence_title(f"2: {text}", text) == "2: Haltestelle Rathaus aufgelassen"


def test_a_baustellen_section_of_50_characters_keeps_its_crossing() -> None:
    title = "Burggasse von Kreuzung Neubaugasse bis Kenyongasse"
    assert len(title) == 50
    assert _compact_baustellen_section(title) == title


def test_a_baustellen_section_of_51_characters_drops_its_crossing() -> None:
    title = "Burggasse von Kreuzung Neubaugasse bis Zieglergasse"
    assert len(title) == 51
    assert _compact_baustellen_section(title) == "Burggasse von Neubaugasse bis Zieglergasse"


def test_a_baustellen_title_of_50_characters_keeps_its_section() -> None:
    title = "Rennweg von Sechskrügelgasse bis Schlachthausgasse"
    assert len(title) == 50
    assert _display_title_parts(_baustelle(title)) == (title, "")


def test_a_baustellen_title_of_51_characters_moves_its_section() -> None:
    title = "Landstraßer Hauptstraße von Juchgasse bis Weyrgasse"
    assert len(title) == 51
    assert _display_title_parts(_baustelle(title)) == ("Landstraßer Hauptstraße", "Von Juchgasse bis Weyrgasse")
