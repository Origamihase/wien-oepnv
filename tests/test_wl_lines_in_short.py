"""A WL disruption on several lines shows every line in short (2026-10-15).

Live on 2026-10-11 from 11:30 to 23:30 Vienna time (``docs/feed.xml``), the
TV read "30A/31A/36B: Gasrohrgebrechen" with the detour of the 36B only: the
summary keeps WL's first sentences up to 180 characters, and WL writes one
sentence per line. Michael's card pick "Kurzform je Linie" (2026-10-15, "Am
TV sieht man sonst nur, was mit einer von drei Linien passiert"): each line
with its measure and its two end points. The shapes below are WL cache
entries of those days (``cache/wl_9d709a/events.json``), read as the feed
reads them; a replay over all 4,899 WL texts since July changed 62.

Mutations checked against this file (each one caught, by the test named):

* the short form is not used → ``test_the_gas_leak_shows_all_three_detours``.
* it applies although every line is shown → ``test_a_text_that_shows_every_line_stays``.
* an unknown measure is shortened anyway → ``test_an_unknown_measure_keeps_the_text``.
* "Dr." ends a place → ``test_an_abbreviation_belongs_to_the_place``.
* lines of different measures share one head → ``test_lines_of_different_measures_name_theirs``.
* the last lines are cut mid-word instead of whole → ``test_lines_that_do_not_fit_give_way_whole``.
* the stripped first label loses its line → ``test_the_text_before_the_first_label_is_the_unnamed_line``.
* "Badner Bahn:" is no label → ``test_badner_bahn_and_bare_measures``.
"""

from __future__ import annotations

from datetime import datetime
from typing import cast

import pytest

import src.build_feed as bf
from src.feed_types import FeedItem

pytestmark = pytest.mark.usefixtures("time_line_today")


def _wl(title: str, description: str) -> FeedItem:
    return {
        "source": "Wiener Linien",
        "category": "Störung",
        "title": title,
        "description": description,
        "link": "https://www.wienerlinien.at/ogd_realtime",
        "guid": f"wl|{title}",
    }


def _summary(item: FeedItem, start: str) -> str:
    starts = datetime.fromisoformat(start)
    return bf._format_item_content(item, "wl", starts, None).desc_text_truncated


GAS = _wl(
    "30A/31A/36B: Gasrohrgebrechen",
    "Die Linie 36B wird in Richtung Jedlesee, Bellgasse zwischen Berzeliusgasse und Brünner "
    "Straße S über Brünner Straße umgeleitet. Die Linie 30A wird in Richtung Stammersdorf, "
    "Freiheitsplatz zwischen Berzeliusgasse und Gschweidlgasse über Brünner Straße, "
    "Hanreitergasse umgeleitet. Die Linie 31A wird in Richtung Großjedlersdorf, "
    "Jochbergengasse zwischen Berzeliusgasse und Großjedlersdorf, Jochbergengasse über "
    "Brünner Straße, Hanreitergasse, Jedlersdorfer Straße umgeleitet. Weichen Sie ersatzweise "
    "auf die Linien 30, 31, 32A aus. Voraussichtliche Dauer: Betriebsschluss. Grund: "
    "Gasrohrgebrechen im Bereich Brünner Straße # Frauenstiftgasse .",
)


def test_the_gas_leak_shows_all_three_detours() -> None:
    assert _summary(GAS, "2026-10-11T11:00:00+02:00").startswith(
        "Umleitungen: 36B Berzeliusgasse – Brünner Straße S; 30A Berzeliusgasse – "
        "Gschweidlgasse; 31A Berzeliusgasse – Großjedlersdorf, Jochbergengasse."
    )


def test_a_text_that_shows_every_line_stays() -> None:
    item = _wl(
        "26/27: Verkehrsunfall",
        "Linie 26: Kein Betrieb zwischen Zanggasse und Josef-Baumann-Gasse. Linie 27: Kein "
        "Betrieb zwischen Berresgasse und Josef-Baumann-Gasse. Die Züge fahren zwischen Aspern "
        "Nord S U und Hausfeldstraße U, bzw. Strebersdorf und Kagran U. Voraussichtliche Dauer: "
        "13:00 Uhr. Grund: Verkehrsunfall im Bereich Kagraner Platz.",
    )
    assert _summary(item, "2026-08-31T12:15:00+02:00").startswith(
        "Linie 26: Kein Betrieb zwischen Zanggasse und Josef-Baumann-Gasse; Linie 27: Kein "
        "Betrieb zwischen Berresgasse und Josef-Baumann-Gasse."
    )


def test_an_unknown_measure_keeps_the_text() -> None:
    # "fährt ersatzweise ab … nach …" is no measure the short form knows.
    item = _wl(
        "12/2: Veranstaltung",
        "Linie 2: Betrieb nur zwischen Friedrich-Engels-Platz und Schwarzenbergplatz. Die Züge "
        "werden weiter zum Karlsplatz umgeleitet. Die Linie 12 fährt ersatzweise ab "
        "Josefstädter Straße nach Dornbach. Voraussichtliche Dauer: 19. September, etwa 01:00 "
        "Uhr. Grund: Veranstaltung im Bereich Josefstädter Straße.",
    )
    assert _summary(item, "2026-09-18T18:00:00+02:00").startswith(
        "Linie 2: Betrieb nur zwischen Friedrich-Engels-Platz und Schwarzenbergplatz."
    )


def test_an_abbreviation_belongs_to_the_place() -> None:
    item = _wl(
        "5B/11A: Feuerwehreinsatz",
        "Die Linie 5B wird in Richtung Praterstern S U zwischen Bhf. Heiligenstadt S U und "
        "Heiligenstädter Brücke über Heiligenstädter Straße und Gunoldstraße umgeleitet. Die "
        "Linie 11A wird in Richtung Stadion U zwischen Bahnhof Heiligenstadt S U und "
        "Dr.-Adolf-Schärf-Heim über Heiligenstädter Straße und Gunoldstraße umgeleitet. "
        "Voraussichtliche Dauer: Nicht absehbar. Grund: Feuerwehreinsatz im Bereich Muthgasse 26.",
    )
    assert _summary(item, "2026-08-04T17:21:00+02:00").startswith(
        "Umleitungen: 5B Bhf. Heiligenstadt S U – Heiligenstädter Brücke; 11A Bahnhof "
        "Heiligenstadt S U – Dr.-Adolf-Schärf-Heim."
    )


def test_lines_of_different_measures_name_theirs() -> None:
    item = _wl(
        "25/26/27/30: Beschädigte Oberleitung durch einen LKW",
        "Linie 25: Betrieb nur zwischen Aspern, Oberdorfstraße und Kagran U. Linie 26: Betrieb "
        "nur zwischen Hausfeldstraße U und Josef-Baumann-Gasse. Weiterfahrt nach Kagran U. Die "
        "Linie 27 fährt derzeit nur zwischen Aspern Nord S U und Josef-Baumann-Gasse. Linie 30: "
        "Derzeit ist ein Betrieb nicht möglich. Weichen Sie ersatzweise auf die Linien 30A, 32A, "
        "34A, 36B und 500 oder die S3 aus. Grund: Beschädigte Oberleitung durch einen LKW im "
        "Bereich Brünner Straße 51.",
    )
    assert _summary(item, "2026-07-21T07:57:00+02:00").startswith(
        "25: Betrieb nur Aspern, Oberdorfstraße – Kagran U; 26: Betrieb nur Hausfeldstraße U – "
        "Josef-Baumann-Gasse; 27: Betrieb nur Aspern Nord S U – Josef-Baumann-Gasse; 30: Kein "
        "Betrieb."
    )


def test_lines_that_do_not_fit_give_way_whole() -> None:
    item = _wl(
        "37/38/40/41/42: Schadhaftes Fahrzeug",
        "Linie 37: Betrieb nur zwischen Hohe Warte und Nußdorfer Straße U. Linie 38: Betrieb nur "
        "zwischen Grinzing und Spitalgasse. Weiterfahrt bis Alser Straße U. Linie 40: Betrieb nur "
        "zwischen Gersthof, Herbeckstraße und Währinger Straße, Volksoper U. Linie 41: Betrieb nur "
        "zwischen Pötzleinsdorf und Währinger Straße, Volksoper U. Linie 42: Betrieb nur zwischen "
        "Antonigasse und Währinger Straße, Volksoper U. Weichen Sie ersatzweise auf die Linien 43 "
        "und 44 aus. Grund: Schadhaftes Fahrzeug im Haltestellenbereich Schwarzspanierstraße.",
    )
    summary = _summary(item, "2026-10-11T18:22:00+02:00")
    assert summary.startswith(
        "Betrieb nur: 37 Hohe Warte – Nußdorfer Straße U; 38 Grinzing – Spitalgasse; 40 Gersthof, "
        "Herbeckstraße – Währinger Straße, Volksoper U …"
    )


def test_the_text_before_the_first_label_is_the_unnamed_line() -> None:
    # ``_post_filter_wl`` strips the first "Linie 37:" before the summary is made.
    text = (
        "Betrieb nur zwischen Hohe Warte und Nußdorfer Straße U. Linie 38: Betrieb nur zwischen "
        "Grinzing und Spitalgasse. Weiterfahrt bis Alser Straße U. Voraussichtlich bis 19:00 Uhr."
    )
    assert bf._lines_in_short(text, "37/38: Schadhaftes Fahrzeug") == (
        "Betrieb nur: 37 Hohe Warte – Nußdorfer Straße U; 38 Grinzing – Spitalgasse. "
        "Voraussichtlich bis 19:00 Uhr."
    )


def test_badner_bahn_and_bare_measures() -> None:
    item = _wl(
        "1/18/62/LB: 1, 18, 62, Badner Bahn: Signalstörung",
        "Linie 1: Unregelmäßige Intervalle in beiden Richtungen. Linie 18: Unregelmäßige "
        "Intervalle in beiden Richtungen. Linie 62: Unregelmäßige Intervalle in beiden "
        "Richtungen. Badner Bahn: Unregelmäßige Intervalle in beiden Richtungen. Grund: "
        "Signalstörung.",
    )
    assert _summary(item, "2026-09-10T06:46:00+02:00").startswith(
        "Unregelmäßige Intervalle: 1, 18, 62, Badner Bahn."
    )


def test_other_sources_are_left_alone() -> None:
    item = cast(FeedItem, {**GAS, "source": "ÖBB"})
    assert not _summary(item, "2026-10-11T11:00:00+02:00").startswith("Umleitungen:")
