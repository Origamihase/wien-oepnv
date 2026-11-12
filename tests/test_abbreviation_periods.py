"""One rule for the period of an abbreviation (``src/feed_build/abbreviations.py``).

WL and ÖBB abbreviate inside running text ("Bhf. Meidling", "Josefstädter
Str.", "Fahrleitungsgebr.", "4. Tor"). Every step that looks for the end of a
sentence, or takes the full stop off one, asks the same rule since
2026-11-12. The published cases below read from WL's cache of their day:

* 12 on 23.10.2026: "Josefstädter Str." made a ticker a long message;
* 59A on 30.10.2026 and 2 on 09.11.2026: the title lost the period of
  "Flurschützstr." / "Fahrleitungsgebr." and the cause stood twice;
* 25/26/27/N20 on 19.08.2026 and D on 22.10.2026: the cut at 180 characters
  dropped "ein." and "der Linien D." as if they were abbreviations;
* N46 on 28.10.2026: "Busse halten bei 13A!." (a sentence closed twice).

Mutations checked against this file (each one caught, by the test named):

* ``is_abbreviation`` without the cut-short rule → ``test_the_shape_of_the_word_decides``.
* without the vowel list → ``test_the_shape_of_the_word_decides``.
* acronyms taken for abbreviations → ``test_a_word_or_a_name_is_no_abbreviation``.
* "Pkw" taken for an abbreviation → ``test_a_word_or_a_name_is_no_abbreviation``.
* every number before a word an ordinal → ``test_an_ordinal_is_told_by_the_word_after_it``.
* the glued initial dropped → ``test_an_ordinal_is_told_by_the_word_after_it``.
* ``without_full_stop`` strips every period → ``test_a_full_stop_goes_and_an_abbreviation_keeps_its_period``.
* ``_is_long_message`` back to ``endswith(".")``, or without the inner sentence
  → ``test_a_long_message_ending_in_an_abbreviation_stays_one``.
* the title strips every final period → ``test_59a_on_30_10``.
* the title splits at an abbreviation → ``test_a_title_sentence_ends_at_its_own_mark``.
* the cut at 180 drops every short word with a period → ``test_the_cut_keeps_a_word_that_ends_its_sentence``.
* the cut at 180 keeps abbreviations → ``test_the_cut_drops_an_abbreviation_whose_name_it_took``.
* the "; " join of tickers strips an abbreviation's period → ``test_a_ticker_join_keeps_the_period_of_an_abbreviation``.
* the "; " join appends "." behind "!" → ``test_n46_on_28_10``.
* the joins beside a long message strip it → ``test_the_consequences_beside_a_long_message_keep_an_abbreviation``,
  ``test_the_measure_of_a_long_message_keeps_an_abbreviation``.
* a listed incident strips it → ``test_the_measure_of_a_long_message_keeps_an_abbreviation``,
  ``test_a_listed_consequence_keeps_an_abbreviation``.
* the stops behind an incident strip it → ``test_the_stops_behind_an_incident_keep_an_abbreviation``.
* the collapse lead ends at "4." → ``test_the_collapse_reads_an_ordinal_as_part_of_its_sentence``.
* a relocation origin or direction loses it → ``test_a_relocation_origin_keeps_the_period_of_an_abbreviation``,
  ``test_a_relocation_direction_keeps_the_period_of_an_abbreviation``.
* a relocated stop's comparison loses it → ``test_a_relocated_stop_ending_in_an_abbreviation_is_recognised``.

In ``tests/test_bhf_cut_and_phrase_stops.py``: the word cut instead of the
consequence alone (``test_the_n8_summary_does_not_end_on_bhf``), the shared
rule in ``_last_sentence_end`` and the capital rule of the stop masking.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest

import src.build_feed as bf
from src.feed.merge import deduplicate_fuzzy
from src.feed_build.abbreviations import is_abbreviation, is_abbreviation_period, without_full_stop
from src.feed_build.display_titles import _first_title_sentence
from src.feed_build.relocation import _relocation_arrows, _relocation_by_direction
from src.feed_build.summary_text import _truncate_summary_180
from src.feed_build.title_words import _last_sentence_end
from src.feed_build.wl_joins import _with_unowned
from src.feed_build.wl_measures import _collapse_repeated_wl_line_segments
from src.feed_build.wl_notices import _relocated_stops_shown
from src.feed_build.wl_ticker_text import _listed_incident, _with_consequences
from src.feed_build.wl_tickers import _is_long_message
from src.feed_types import FeedItem

# Rendered time lines name no year and no "Heute" relative to a fixed day.
pytestmark = pytest.mark.usefixtures("time_line_today")


# ---------------- the rule ----------------


@pytest.mark.parametrize(
    "word",
    ["Bhf", "Hbf", "Hlst", "Str", "St", "Nr", "Wr", "bzw", "zw", "b", "u", "Fahrleitungsgebr", "Hauptstr",
     "Hausnr", "Dörfelstr", "ca", "ggü", "prov", "geg", "Li", "Ri", "haltest", "gem", "mind", "ONr", "usw",
     "etc", "sog", "evtl", "inkl"],
)
def test_the_shape_of_the_word_decides(word: str) -> None:
    assert is_abbreviation(word)


@pytest.mark.parametrize(
    "word",
    ["Uhr", "aus", "ein", "Verkehr", "Herr", "Tor", "Juni", "PKW", "Pkw", "Lkw", "Kfz", "KG", "WLB", "GmbH",
     "U", "S", "D", "O", "S80", "N20", "13A", "Verkehrsunfall", "Kagran", "12", "Bhf3", ""],
)
def test_a_word_or_a_name_is_no_abbreviation(word: str) -> None:
    assert not is_abbreviation(word)


@pytest.mark.parametrize(
    ("text", "before", "expected"),
    [
        ("Zentralfriedhof, 4. Tor", "4", True),
        ("bis 03. Juni 2026", "03", True),
        ("im 1. Bezirk", "1", True),
        ("die Linie 49. Voraussichtliche Dauer", "49", False),
        ("Ersatz: Linien 9 und 43. Linie 10:", "43", False),
        ("Aspern Nord ü. J-Baumann-G.", "G", True),
        ("Richtung Kaisermühlen, V.I.C. U", "C", True),
        ("Betrieb ab Karlsplatz U. Grund", "U", False),
        ("Fahrt bis Wien Hbf. Richtung Süden", "Hbf", True),
        ("(Simmeringer Platz, Busbahnhof, 3. und 4. Haltestelle)", "4", True),
        ("(Praterstern, 3. Haltestelleinsel unter dem Vordach)", "3", True),
        ("über die Strecke der Linien 43 und 9. Haltestelle Mayssengasse", "9", False),
        # A bracket that closed before the number does not count.
        ("Thaliastraße (Gürtelquerung, Haltestelleninsel) verlegt nach Thaliastraße 3. Aufgelassene", "3", False),
        ("Linie N49 (Mo-Fr): Busse halten bei Bergmillergasse 5. Linie N49 (Mo-Fr)", "5", False),
        ("Von: 1. Haidequerstraße 2", "1", True),
        ("Züge halten bei Li. 6. Ri. Geiereckstr.", "6", True),
        # Three digits or a number glued to a word are no ordinal.
        ("Simmeringer Hauptstraße 197. Juni", "197", False),
        ("über Gleis 2. Voraussichtliche Dauer", "2", False),
        ("Linie U6. Juni", "U6", False),
    ],
)
def test_an_ordinal_is_told_by_the_word_after_it(text: str, before: str, expected: bool) -> None:
    index = text.index(f"{before}.") + len(before)
    assert is_abbreviation_period(text, index) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Betrieb ab Karlsplatz.", "Betrieb ab Karlsplatz"),
        ("Betrieb nur bis Bhf. Meidling Dörfelstr.", "Betrieb nur bis Bhf. Meidling Dörfelstr."),
        ("Kein Betrieb. ", "Kein Betrieb"),
        ("Haltestelle: Kaisermühlen, V.I.C.", "Haltestelle: Kaisermühlen, V.I.C."),
        ("Linie 18 benutzen!", "Linie 18 benutzen!"),
    ],
)
def test_a_full_stop_goes_and_an_abbreviation_keeps_its_period(text: str, expected: str) -> None:
    assert without_full_stop(text) == expected


# ---------------- the cut behind a whole sentence ----------------


def test_an_acronym_ends_a_sentence() -> None:
    text = "Unfall durch einen PKW. Grund: Fremder Verkehrsunfall"
    end = _last_sentence_end(text, len(text))
    assert end is not None and text[:end].endswith("PKW.")


def test_a_cut_short_word_ends_no_sentence() -> None:
    text = "Oberleitung durch Fahrleitungsgebr. Betrieb ab Kagran"
    assert _last_sentence_end(text, len(text)) is None


# ---------------- the cut at 180 characters ----------------


@pytest.mark.parametrize(
    ("text", "ending"),
    [
        (
            "Die Gleise in der Donaufelder Straße werden erneuert, daher kommt es zu Einschränkungen bei den "
            "Linien 25, 26, 27 und N20. Planen Sie ca. 15 Minuten mehr Fahrzeit ein. Zeitraum: Montag, "
            "14. September 2026 bis Ende 2026.",
            "mehr Fahrzeit ein. …",
        ),
        (
            "Staatsbesuch: Kein Betrieb zwischen Börse und Quartier Belvedere S. Weiterfahrt über Linie 18 bis "
            "St. Marx S. Beachten Sie die baustellenbedingte Streckensperre der Linien D. Ersatz: Linie 71.",
            "Streckensperre der Linien D. …",
        ),
    ],
)
def test_the_cut_keeps_a_word_that_ends_its_sentence(text: str, ending: str) -> None:
    assert _truncate_summary_180(text).endswith(ending)


def test_the_cut_drops_an_abbreviation_whose_name_it_took() -> None:
    text = (
        "Die Linie N8 wird in beiden Richtungen zwischen Schedifkaplatz und Hans-Mandl-Berufsschule über "
        "Eichenstraße und Längenfeldgasse umgeleitet. Ersatzhaltestelle gegenüber Bhf. Meidling"
    )
    assert _truncate_summary_180(text).endswith("Ersatzhaltestelle gegenüber …")


# ---------------- tickers and long messages ----------------


def test_a_long_message_ending_in_an_abbreviation_stays_one() -> None:
    assert _is_long_message("Die Linie 12 hält nicht. Ersatz: Linie 5 bis Josefstädter Str.")
    assert _is_long_message("Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen.")
    assert not _is_long_message("Züge halten bei Linie 12 Richtung Josefstädter Str.")
    assert not _is_long_message("Betrieb nur bis Bhf. Meidling Dörfelstr.")


def test_an_inner_exclamation_or_question_ends_a_sentence() -> None:
    assert _is_long_message("Kein Halt! Züge halten bei Josefstädter Str.")
    assert _is_long_message("Wohin? Die Busse halten bei Josefstädter Str.")


def test_a_ticker_ending_in_an_initial_is_no_long_message() -> None:
    assert not _is_long_message("Aspern Nord ü. J-Baumann-G.")


def _wl(title: str, description: str, start: str, end: str | None, guid: str) -> FeedItem:
    return cast(FeedItem, {
        "source": "Wiener Linien", "category": "Störung", "title": title, "description": description,
        "link": "https://www.wienerlinien.at/ogd_realtime", "guid": guid, "pubDate": start, "starts_at": start,
        "ends_at": end, "_identity": f"wl|{guid}",
    })


def _shown(items: list[FeedItem]) -> list[tuple[str, str]]:
    """Title and text ``main`` renders of *items* (read, deduplicated, merged), without the time line."""
    read = bf._normalize_item_datetimes(bf._post_filter_wl(list(items)))
    deduped = cast(list[FeedItem], deduplicate_fuzzy(cast(list[dict[str, Any]], bf._dedupe_items(read))))
    shown = []
    for item in bf._merge_wl_ticker_clusters(deduped):
        starts = item.get("starts_at")
        ends = item.get("ends_at")
        formatted = bf._format_item_content(
            item, ident="t", starts_at=starts if isinstance(starts, datetime) else None,
            ends_at=ends if isinstance(ends, datetime) else None, split_reason=True,
        )
        shown.append((formatted.title_out, formatted.desc_text_truncated.rsplit(" [", 1)[0]))
    return shown


def test_line_12_on_23_10() -> None:
    items = [
        _wl("12: Fremder Verkehrsunfall", "Linie 12: Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen.",
            "2026-10-23T15:14:00+02:00", None, "long"),
        _wl("12: Fahrtbehinderung Fremder Verkehrsunfall", "Fahrtbehinderung\nFremder Verkehrsunfall",
            "2026-10-23T15:15:05+02:00", "2026-10-23T16:14:00+02:00", "hindrance"),
        _wl("12: Fremdunfall Züge halten bei Linie 12 Richtung Josefstädter Str.",
            "Fremdunfall\nZüge halten bei Linie 12 Richtung Josefstädter Str.",
            "2026-10-23T15:15:05+02:00", "2026-10-23T16:27:00+02:00", "stop"),
    ]
    # Published: "12: Fremdunfall" over "Fremdunfall Züge halten bei Linie 12
    # Richtung Josefstädter Str. Fremder Verkehrsunfall. [Seit 15:14]".
    assert _shown(items) == [
        ("12: Fremder Verkehrsunfall", "Züge halten bei Linie 12 Richtung Josefstädter Str."),
    ]


def test_59a_on_30_10() -> None:
    items = [
        _wl("59A: Feuerwehreinsatz Umleitung ab Arbeitergasse über Gürtel - Flurschützstr.",
            "Feuerwehreinsatz\nUmleitung ab Arbeitergasse über Gürtel - Flurschützstr.",
            "2026-10-30T18:18:53+01:00", "2026-10-30T23:30:00+01:00", "59a"),
    ]
    # Published: "Feuerwehreinsatz Umleitung ab Arbeitergasse über Gürtel - Flurschützstr."
    assert _shown(items) == [
        ("59A: Feuerwehreinsatz", "Umleitung ab Arbeitergasse über Gürtel - Flurschützstr."),
    ]


def test_line_2_on_09_11() -> None:
    items = [
        _wl("2: Fahrleitungsgebr. Betrieb ab Am Tabor", "Fahrleitungsgebr.\nBetrieb ab Am Tabor",
            "2026-11-09T12:02:52+01:00", "2026-11-09T13:00:00+01:00", "consequence"),
        _wl("2: Fahrtbehinderung wegen Fahrleitungsgebr.", "Fahrtbehinderung\nwegen Fahrleitungsgebr.",
            "2026-11-09T12:01:22+01:00", "2026-11-09T12:59:00+01:00", "hindrance"),
    ]
    # Published: "2: Fahrleitungsgebr" over "Betrieb ab Am Tabor. Fahrtbehinderung
    # wegen Fahrleitungsgebr." Now as any other cause ("2: Rettungseinsatz").
    assert _shown(items) == [("2: Fahrleitungsgebr.", "Fahrtbehinderung; Betrieb ab Am Tabor.")]


def test_n46_on_28_10() -> None:
    items = [
        _wl("N46: Fahrtbehinderung Fremder Verkehrsunfall", "Fahrtbehinderung\nFremder Verkehrsunfall",
            "2026-10-28T01:57:02+01:00", "2026-10-28T02:56:00+01:00", "hindrance"),
        _wl("N46: Fremdunfall Busse halten bei 13A!", "Fremdunfall\nBusse halten bei 13A!",
            "2026-10-28T01:57:02+01:00", "2026-10-28T03:01:00+01:00", "stop"),
        _wl("N46: Fremder Verkehrsunfall", "Linie N46: Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen.",
            "2026-10-28T01:59:00+01:00", None, "long"),
    ]
    ((title, text),) = _shown(items)
    assert "!." not in text
    assert text.startswith("Busse halten bei 13A!")


def test_a_ticker_join_keeps_the_period_of_an_abbreviation() -> None:
    items = [
        _wl("62: Gleisschaden Betrieb nur bis Bhf. Meidling Dörfelstr.", "Gleisschaden\nBetrieb nur bis Bhf. Meidling <> Dörfelstr.",
            "2026-06-03T09:00:00+02:00", "2026-06-03T12:00:00+02:00", "first"),
        _wl("62: Gleisschaden Betrieb ab Bhf. Meidling Dörfelstraße", "Gleisschaden\nBetrieb ab Bhf. Meidling <> Dörfelstraße",
            "2026-06-03T09:01:00+02:00", "2026-06-03T12:00:00+02:00", "second"),
    ]
    ((title, text),) = _shown(items)
    assert title == "62: Gleisschaden"
    assert "Dörfelstr.; Betrieb ab" in text


# ---------------- titles, segments, relocations ----------------


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("Umleitung ab Arbeitergasse über Gürtel - Flurschützstr.", "Umleitung ab Arbeitergasse über Gürtel - Flurschützstr."),
        ("Haltestelle Stadionbrücke aufgelassen. Bitte ausweichen.", "Haltestelle Stadionbrücke aufgelassen"),
        ("Fahrleitungsgebr. Betrieb ab Kagran. Bitte ausweichen.", "Fahrleitungsgebr. Betrieb ab Kagran"),
        ("Gleisschaden Linie 18 benutzen!", "Gleisschaden Linie 18 benutzen"),
        # The line "D." and the marker "U." end a sentence as in every step.
        ("Streckensperre der Linie D. Betrieb ab Nußdorf", "Streckensperre der Linie D"),
        ("Gasrohrgebr. Kein Halt!!! Bitte ausweichen", "Gasrohrgebr. Kein Halt"),
    ],
)
def test_a_title_sentence_ends_at_its_own_mark(body: str, expected: str) -> None:
    assert _first_title_sentence(body) == expected


def test_the_collapse_reads_an_ordinal_as_part_of_its_sentence() -> None:
    desc = (
        "Linie 6: Kein Betrieb zwischen Simmering und Zentralfriedhof, 4. Tor. "
        "Linie 71: Kein Betrieb zwischen Simmering und Zentralfriedhof, 4. Tor. Weichen Sie auf die Linie 11 aus."
    )
    assert _collapse_repeated_wl_line_segments(desc) == (
        "Linie 6: Kein Betrieb zwischen Simmering und Zentralfriedhof, 4. Tor. Weichen Sie auf die Linie 11 aus."
    )


def test_a_relocation_origin_keeps_the_period_of_an_abbreviation() -> None:
    assert _relocation_arrows(["Von: Josefstädter Str.", "Nach: Lange Gasse 5"]) == ["Josefstädter Str. → Lange Gasse 5"]
    assert _relocation_arrows(["Von: Wilhelmstraße 25.", "Nach: Wilhelmstraße 27"]) == ["Wilhelmstraße 25 → Wilhelmstraße 27"]


def test_a_relocation_direction_keeps_the_period_of_an_abbreviation() -> None:
    blocks = ["Fahrtrichtung: Josefstädter Str.", "Von: Lange Gasse 5", "Nach: Lange Gasse 9",
              "Fahrtrichtung: Ottakring.", "Von: Lange Gasse 8", "Nach: Lange Gasse 12"]
    assert _relocation_by_direction(blocks) == [
        "Richtung Josefstädter Str.: Lange Gasse 5 → Lange Gasse 9",
        "Richtung Ottakring: Lange Gasse 8 → Lange Gasse 12",
    ]


# ---------------- WL texts joined with "; " ----------------

NOW = datetime(2026, 11, 12, 8, 0, tzinfo=UTC)


def _item(title: str, description: str, guid: str, *, category: str = "Störung") -> FeedItem:
    return cast(FeedItem, {
        "source": "Wiener Linien", "category": category, "title": title, "description": description, "guid": guid,
        "link": "", "pubDate": NOW, "starts_at": NOW, "ends_at": None, "_identity": f"wl|{guid}",
    })


LONG_62 = _item(
    "62: Gleisschaden",
    "Die Linie 62 fährt derzeit nicht zwischen Dörfelstraße und Hofwiesengasse. Voraussichtliche Dauer: "
    "22:00 Uhr. Grund: Gleisschaden.",
    "long",
)
BUSES_62A = _item("62: Gleisschaden Busse halten bei Linie 62A", "Gleisschaden\nBusse halten bei Linie 62A", "buses")


def test_the_consequences_beside_a_long_message_keep_an_abbreviation() -> None:
    trams = _item("62: Gleisschaden Züge halten bei Linie 6 Richtung Josefstädter Str.",
                  "Gleisschaden\nZüge halten bei Linie 6 Richtung Josefstädter Str.", "trams")
    assert str(_with_consequences(LONG_62, [trams, BUSES_62A])["description"]).startswith(
        "Züge halten bei Linie 6 Richtung Josefstädter Str.; Busse halten bei Linie 62A. Die Linie 62"
    )
    waits = _item("62: Gleisschaden Derzeit längere Wartezeiten bei Bhf. Meidling Dörfelstr.",
                  "Gleisschaden\nDerzeit längere Wartezeiten bei Bhf. Meidling Dörfelstr.", "waits")
    gaps = _item("62: Gleisschaden Derzeit unregelmäßige Intervalle", "Gleisschaden\nDerzeit unregelmäßige Intervalle", "gaps")
    assert str(_with_consequences(LONG_62, [waits, gaps])["description"]).endswith(
        "Grund: Gleisschaden. Derzeit längere Wartezeiten bei Bhf. Meidling Dörfelstr.; Derzeit unregelmäßige Intervalle."
    )


def test_the_measure_of_a_long_message_keeps_an_abbreviation() -> None:
    long_message = _item(
        "62: Gleisschaden",
        "Betrieb nur zwischen Schottenring und Josefstädter Str. Voraussichtliche Dauer: 14:45 Uhr. Grund: Gleisschaden.",
        "long2",
    )
    assert _with_consequences(long_message, [BUSES_62A])["description"] == (
        "Betrieb nur zwischen Schottenring und Josefstädter Str.; Busse halten bei Linie 62A. "
        "Voraussichtliche Dauer: 14:45 Uhr. Grund: Gleisschaden."
    )
    assert _listed_incident(long_message) == ("Gleisschaden", "Betrieb nur zwischen Schottenring und Josefstädter Str.")


def test_a_listed_consequence_keeps_an_abbreviation() -> None:
    ticker = _item("62: Gleisschaden Betrieb nur bis Josefstädter Str.", "Gleisschaden\nBetrieb nur bis Josefstädter Str.", "t")
    assert _listed_incident(ticker)[1].endswith("Betrieb nur bis Josefstädter Str.")


def test_the_stops_behind_an_incident_keep_an_abbreviation() -> None:
    delay = _item("25: Verspätungen", "Verspätungen\nWegen eines schadhaften Fahrzeugs", "delay")
    stops = [
        _item("25: Ersatzbus hält Donaufelder Str.", "Ersatzbus hält Donaufelder Str.", "s1"),
        _item("25: Züge halten Donaufelder Straße 175", "Züge halten Donaufelder Straße 175", "s2"),
    ]
    assert str(_with_unowned(delay, stops)["description"]).endswith(
        "Ersatzbus hält Donaufelder Str.; Züge halten Donaufelder Straße 175."
    )


def test_a_relocated_stop_ending_in_an_abbreviation_is_recognised() -> None:
    notice = _item("2: Haltestellenverlegung", "Haltestelle: Josefstädter Str.\nVon: Josefstädter Straße 1\nNach: Josefstädter Str.",
                   "notice", category="Hinweis")
    assert _relocated_stops_shown(_item("2: Busse halten Josefstädter Str.", "Busse halten Josefstädter Str.", "t"), notice)


# ---------------- found by the independent check of 12.11.2026 ----------------


def test_a_title_that_lost_only_its_closing_mark_is_not_repeated() -> None:
    # WL's cache of 04.05.2026: title and text "Gasrohrgebr. Kein Halt!!!".
    items = [
        _wl("18: Gasrohrgebr. Kein Halt!!!", "Gasrohrgebr. Kein Halt!!!",
            "2026-05-04T14:40:54+02:00", "2026-05-04T23:45:00+02:00", "18"),
    ]
    assert _shown(items) == [("18: Gasrohrgebr. Kein Halt", "[Am\u00a004.05.]")]


def test_the_lead_cut_ends_behind_a_stop_marker() -> None:
    # Line 6 on 21.08.2026: WL's "… über Hauptbahnhof S U. Grund: …".
    from src.feed_build.title_words import _lead_with_fragment

    text = _lead_with_fragment(
        "Betrieb ab Matzleinsdorfer Platz",
        "Umleitung in Richtung Burggasse, Stadthalle U zwischen Quellenplatz und Matzleinsdorfer Platz S "
        "über Hauptbahnhof S U. Grund: Polizeieinsatz im Haltestellenbereich Bernhardtstalgasse.",
    )
    assert text.endswith("über Hauptbahnhof S U.")


def test_the_lead_cut_counts_a_sentence_that_ends_at_the_limit() -> None:
    # 13A on 26.09.2026: "… 48A aus." ends at character 180, the capital
    # behind it lies past the limit. The text ended "… Neubaugasse U." and
    # dropped the lines to use instead.
    from src.feed_build.title_words import _last_sentence_end, _lead_with_fragment

    text = _lead_with_fragment(
        "Busse halten Amerlingstraße 8-10",
        "Die Linie 13A fährt derzeit nur zwischen Hauptbahnhof S U und Neubaugasse U. Weichen Sie "
        "ersatzweise auf die Linien U3, 5, 12, 46, 52 und 48A aus. Voraussichtliche Dauer: ca. 22 Uhr. "
        "Grund: Veranstaltung im Bereich Neubaugasse.",
    )
    assert len(text) == 180
    assert text.endswith("auf die Linien U3, 5, 12, 46, 52 und 48A aus.")
    assert _last_sentence_end("Eins. Zwei. Drei", 11) == 11
    assert _last_sentence_end("Eins. Zwei. Drei", 10) == 5


def test_the_cut_keeps_a_number_that_ends_its_sentence() -> None:
    text = (
        "Wegen eines Gasrohrgebrechens kommt es zu Einschränkungen bei den Linien 6 und 71 in Wien. Grund "
        "dafür ist ein Gasrohrgebrechen im Bereich Simmeringer Hauptstraße 26. Das Ende der Störung ist offen."
    )
    assert _truncate_summary_180(text).endswith("Simmeringer Hauptstraße 26. …")
    # The cut falls right behind "03.": an ordinal whose month it took goes.
    head = (
        "Wegen Bauarbeiten fahren die Busse der Linie 13A zwischen Neubaugasse und Burggasse eine "
        "Umleitung in beiden Richtungen und über die Lerchenfelder Straße. Nun verlegt bis 03."
    )
    assert _truncate_summary_180(f"{head} Juni 2026.").endswith("Nun verlegt …")
    assert _truncate_summary_180(f"{head} Die Busse fahren wieder.").endswith("Nun verlegt bis 03. …")


def test_the_line_ticker_join_keeps_the_period_of_an_abbreviation() -> None:
    from src.feed_build.wl_joins import _with_line_tickers

    def item(title: str, text: str, guid: str) -> FeedItem:
        return cast(FeedItem, {"title": title, "description": text, "source": "Wiener Linien",
                               "category": "Störung", "guid": guid})

    merged = _with_line_tickers(
        item("13A/14A/57A: Verkehrsunfall", "Unregelmäßige Intervalle in beiden Richtungen.", "e1"),
        [item("13A: Umleitung über Josefstädter Str.", "Umleitung über Josefstädter Str.", "t1"),
         item("57A: Busse halten Burggasse", "Busse halten Burggasse", "t2")],
    )
    assert "Josefstädter Str.; 57A: Busse halten Burggasse." in str(merged["description"])
