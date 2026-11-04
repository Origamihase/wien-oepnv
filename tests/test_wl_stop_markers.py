"""Stops show without Wiener Linien's S-/U-Bahn marker (operator decision 2026-11-04).

"N71: Rennweg S" stood in the feed from 22.10. to 06.11.2026; the operator
wants "Rennweg", in titles and texts, for every stop with such a marker.
"""

from __future__ import annotations

import csv
import re

import pytest

from src.feed_build.item_format import _format_item_content
from src.utils import wl_stop_markers
from src.utils.wl_stop_markers import without_stop_markers


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        ("N71: Rennweg S", "N71: Rennweg"),
        ("Fahrtbehinderung in Richtung Floridsdorf S U. Grund: Rettungseinsatz.",
         "Fahrtbehinderung in Richtung Floridsdorf. Grund: Rettungseinsatz."),
        ("zwischen Kagran U und Praterstern S U.", "zwischen Kagran und Praterstern."),
        ("Richtung Bhf. Atzgersdorf S: Hermesstraße", "Richtung Bhf. Atzgersdorf: Hermesstraße"),
        ("Parlament, U Volkstheater – Schwedenplatz U;", "Parlament, Volkstheater – Schwedenplatz;"),
        ("Stadion U, Handelskai", "Stadion, Handelskai"),
        ("ENKPLATZ U, GRILLGASSE", "ENKPLATZ, GRILLGASSE"),
        # The directory names the stop without a marker, WL's text has one.
        ("die Station Tscherttegasse U in Fahrtrichtung Siebenhirten U",
         "die Station Tscherttegasse in Fahrtrichtung Siebenhirten"),
        ("über Michelbeuern-AKH U …", "über Michelbeuern-AKH …"),
        ("Michelbeuern U AKH", "Michelbeuern AKH"),
        # A text cut inside the name keeps the place.
        ("in Richtung Floridsdorf S…", "in Richtung Floridsdorf…"),
    ],
)
def test_a_stop_shows_without_its_marker(text: str, shown: str) -> None:
    assert without_stop_markers(text) == shown


@pytest.mark.parametrize(
    "text",
    [
        "Unregelmäßige Intervalle in beiden Richtungen U. Linie 36A",  # cut before "U6"
        "U: WE LOVE SONG CONTEST AND U",
        "Ein Schienenersatzverkehr mit Autobussen wird für S …",
        "der S-Bahn und der U-Bahn",
        "Die Linie U6 fährt",
        "Kagran U 26A",  # a stop area of bus 26A, not a marker
        "Linien S 45 und S 50",
        "Matzleinsdorfer Plaz S",  # WL's typo stays (decision 2026-10-29, #2023)
    ],
)
def test_no_stop_no_change(text: str) -> None:
    assert without_stop_markers(text) == text


def test_every_marked_stop_of_the_directory_loses_its_marker() -> None:
    # Guard for every directory refresh: each stop WL names with a marker
    # shows without it (a marker before a number, "Kagran U 26A", is none).
    marker = re.compile(r"(?:^|\s)[SU](?=$|[\s,(])(?!\s*\d)")
    path, column = wl_stop_markers._WL_STOP_LISTS[0]
    with path.open(encoding="utf-8-sig") as handle:
        names = {" ".join(row[column].split()) for row in csv.DictReader(handle, delimiter=";")}
    marked = sorted(n for n in names if marker.search(n))
    assert len(marked) > 100
    for name in marked:
        assert not marker.search(without_stop_markers(name)), name


def test_the_feed_item_reads_rennweg() -> None:
    item = {
        "source": "Wiener Linien",
        "category": "Hinweis",
        "title": "N71: Rennweg S",
        "description": (
            "Rennweg 20-24 → Rennweg 20-24, Hauptfahrbahn. Haltestellenverlegung der Linie N71 "
            "in Richtung Kaiserebersdorf, Zinnergasse. Haltestelle: Rennweg S. Grund: Gleisbau."
        ),
        "guid": "rennweg-n71",
    }
    formatted = _format_item_content(item, "rennweg-n71", None, None)
    assert formatted.title_out == "N71: Rennweg"
    assert " S." not in formatted.desc_text_truncated
    assert "Rennweg S" not in formatted.desc_cdata


def test_a_marker_behind_an_html_entity_goes_too() -> None:
    # 72A, October 2026: WL wrote "Haidestra&szlig;e S" in the HTML; the
    # "Haltestelle:" line only repeats the title once both read "Haidestraße".
    item = {
        "source": "Wiener Linien",
        "category": "Hinweis",
        "title": "72A: Haidestraße S",
        "description": (
            "<p>Haltestellenverlegung der Linie 72A in Richtung Hasenleiten</p>"
            "<p>Haltestelle: Haidestra&szlig;e S</p>"
            "<p>Von: Lautenschl&auml;gergasse nach Haidestra&szlig;e</p>"
            "<p>Nach: 1. Haidequerstra&szlig;e gegen&uuml;ber 3A</p>"
            "<p>Grund: Arbeiten an einer Gasleitung</p>"
        ),
        "guid": "haidestrasse-72a",
    }
    formatted = _format_item_content(item, "haidestrasse-72a", None, None)
    assert formatted.title_out == "72A: Haidestraße"
    assert "Haltestelle:" not in formatted.desc_text_truncated
    assert "Grund: Arbeiten an einer Gasleitung." in formatted.desc_text_truncated
