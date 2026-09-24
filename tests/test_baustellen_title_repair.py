"""A Baustellen title cut by the city is completed from its own description.

Published 2026-09-24 in ``docs/feed.xml`` (item 8)::

    U4: Vordere Zollamtsstraße von Marxergasse und Kleine Marxerbrücke bis
    Unbenannte Verkehrsfläche und Rad…

Stadt Wien caps ``BEZEICHNUNG`` at 100 characters; the cache only marks the
cut (``tests/test_baustellen_truncated_title.py``). The description of the
same item names the endpoint in full, so the feed completes the cut word
from there — and only from there: a fragment with two possible completions
("Radetzkybrücke", "Radweg") is resolved by the range preposition in front
of the candidate, and an ambiguous or missing completion leaves the title
alone. "Unbenannte Verkehrsfläche", the city's placeholder for an unnamed
road segment, is dropped from an endpoint list that also names a real
place.

Measured over the 22 distinct titles cached since June 2026: 3 truncated,
2 completable, 1 not; 1 with the placeholder.

Mutations checked against this file (each one caught, by the test named):

* the repair is not called from ``_post_filter_baustellen`` →
  ``test_the_post_filter_ships_the_repaired_title``.
* the preposition filter is dropped (first candidate wins) →
  ``test_an_ambiguous_fragment_without_a_range_phrase_is_left_alone``.
* the minimum fragment length is dropped →
  ``test_a_fragment_shorter_than_three_letters_is_left_alone``.
* the placeholder is also dropped as the only endpoint →
  ``test_the_placeholder_stays_as_the_only_endpoint``.
* the completion keeps the ellipsis →
  ``test_the_live_titles`` (``…bis Schlachthausgasse`` ends the title).
"""

from __future__ import annotations

import pytest

from src import build_feed
from src.build_feed import _post_filter_baustellen, _repair_baustellen_title
from src.feed_types import FeedItem

# Verbatim from cache/baustellen_d438c3/events.json (2026-09-24).
U4_TITLE = (
    "Vordere Zollamtsstraße von Marxergasse und Kleine Marxerbrücke bis "
    "Unbenannte Verkehrsfläche und Rad…"
)
U4_DESCRIPTION = (
    "Aufgrund der Sanierung der Tunneldecke der U4-Trasse bzw. der dafür "
    "erforderlichen Vorarbeiten im Fahrbahnbereich sowie der Instandsetzung von "
    "Schächten durch die Wiener Netze - Bereich Fernwärme wird die Vordere "
    "Zollamtsstraße im Abschnitt von der Kleine Marxerbrücke bis und in Richtung "
    "zur Radetzkybrücke als provisorische Einbahn bei Aufrechterhaltung von zwei "
    "Fahrspuren geführt. Die Fahrtrichtung zum Heumarkt wird über die "
    "Uraniastraße und den Stubenring umgeleitet. Der Radweg wird über die "
    "Schallautzerstraße bzw. den Stubenring umgeleitet. NEU: Ab 28.09.2026 ist "
    "in Fahrtrichtung Heumarkt der äußerst rechte Fahrstreifen gesperrt.Die "
    "restlichen Fahrstreifen werden aufrechtgehalten. Der Geh- und Radweg auf "
    "Seiten des Wienflusses von Marxerstraße bis Zollamtssteg bleibt weiterhin "
    "gesperrt. Der Radweg wird über die Schallautzerstraße bzw. den Stubenring "
    "umgeleitet. \nBeginn: 01.02.2026 00:00 Uhr \nGeplant bis: 30.11.2026 00:00 "
    "Uhr \nMaßnahme: Gleisbau \nBezirk: 3"
)
U4_REPAIRED = "Vordere Zollamtsstraße von Marxergasse und Kleine Marxerbrücke bis Radetzkybrücke"

LANDSTRASSE_TITLE = (
    "Landstraßer Hauptstraße von Emmerich-Teuber-Platz und Juchgasse und "
    "Apostelgasse bis Schlachthausgas…"
)
LANDSTRASSE_DESCRIPTION = (
    "… Schlachthausgasse zur Einbahn. Die Umleitung in Fahrtrichtung stadteinwärts "
    "erfolgt über: Schlachthausgasse - Rennweg - Oberzellergasse - Landstraßer "
    "Hauptstraße. \nBeginn: 09.03.2026 00:00 Uhr"
)
KENNEDY_TITLE = (
    "Kennedybrücke zwischen Schönbrunner Schloßstraße und Hadikgasse, auf Seite "
    "Otto Wagner Hofpavillon…"
)
KENNEDY_DESCRIPTION = (
    "Die Kennedybrücke wird in Fahrtrichtung 14. Bezirk(Hadikgasse) gesperrt. "
    "\nBeginn: 14.09.2026 00:00 Uhr"
)


@pytest.mark.parametrize(
    ("title", "description", "expected"),
    [
        (U4_TITLE, U4_DESCRIPTION, U4_REPAIRED),
        (
            LANDSTRASSE_TITLE,
            LANDSTRASSE_DESCRIPTION,
            "Landstraßer Hauptstraße von Emmerich-Teuber-Platz und Juchgasse und "
            "Apostelgasse bis Schlachthausgasse",
        ),
        # "Hofpavillon" is a whole word: nothing in the description extends
        # it, so the marker stays and nothing is invented.
        (KENNEDY_TITLE, KENNEDY_DESCRIPTION, KENNEDY_TITLE),
    ],
)
def test_the_live_titles(title: str, description: str, expected: str) -> None:
    assert _repair_baustellen_title(title, description) == expected


def test_two_candidates_are_resolved_by_the_range_preposition() -> None:
    # "Der Radweg" is a subject, "zur Radetzkybrücke" an endpoint.
    out = _repair_baustellen_title("Vordere Zollamtsstraße bis Rad…", U4_DESCRIPTION)
    assert out == "Vordere Zollamtsstraße bis Radetzkybrücke"


def test_an_ambiguous_fragment_without_a_range_phrase_is_left_alone() -> None:
    title = "Vordere Zollamtsstraße bis Rad…"
    description = "Der Radweg und die Radetzkybrücke bleiben gesperrt."
    assert _repair_baustellen_title(title, description) == title


def test_two_candidates_after_prepositions_are_left_alone() -> None:
    title = "Vordere Zollamtsstraße bis Rad…"
    description = "Gesperrt bis Radetzkybrücke, Umleitung zum Radweg."
    assert _repair_baustellen_title(title, description) == title


def test_a_fragment_shorter_than_three_letters_is_left_alone() -> None:
    title = "Rechte Wienzeile von Kreuzung Ramperstorffergasse bis Pi…"
    description = "Von der Ramperstorffergasse bis zur Pilgramgasse gesperrt."
    assert _repair_baustellen_title(title, description) == title


def test_a_hyphenated_completion_is_taken_whole() -> None:
    title = "Landstraßer Hauptstraße bis Emmerich-Teu…"
    description = "Die Sperre reicht bis zum Emmerich-Teuber-Platz."
    assert _repair_baustellen_title(title, description) == (
        "Landstraßer Hauptstraße bis Emmerich-Teuber-Platz"
    )


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        (
            "Vordere Zollamtsstraße von Marxergasse bis Unbenannte Verkehrsfläche und Radetzkybrücke",
            "Vordere Zollamtsstraße von Marxergasse bis Radetzkybrücke",
        ),
        (
            "Vordere Zollamtsstraße von Unbenannte Verkehrsfläche und Marxergasse bis Radetzkybrücke",
            "Vordere Zollamtsstraße von Marxergasse bis Radetzkybrücke",
        ),
        (
            "Vordere Zollamtsstraße von Marxergasse und Unbenannte Verkehrsfläche bis Radetzkybrücke",
            "Vordere Zollamtsstraße von Marxergasse bis Radetzkybrücke",
        ),
        (
            "Vordere Zollamtsstraße von Marxergasse bis Radetzkybrücke und Unbenannte Verkehrsfläche",
            "Vordere Zollamtsstraße von Marxergasse bis Radetzkybrücke",
        ),
    ],
)
def test_the_placeholder_is_dropped_next_to_a_named_endpoint(title: str, expected: str) -> None:
    assert _repair_baustellen_title(title, "") == expected


def test_the_placeholder_stays_as_the_only_endpoint() -> None:
    title = "Vordere Zollamtsstraße von Marxergasse bis Unbenannte Verkehrsfläche"
    assert _repair_baustellen_title(title, "") == title


@pytest.mark.parametrize(
    "title",
    [
        "Rechte Wienzeile von Kreuzung Ramperstorffergasse bis Kreuzung Pilgramgasse und Pilgrambrücke",
        "Favoritenstraße von Kreuzung Landgutgasse bis Kreuzung Keplerplatz und Keplergasse",
        "Baustelle Maxingstraße",
        "",
    ],
)
def test_a_complete_title_is_untouched(title: str) -> None:
    assert _repair_baustellen_title(title, U4_DESCRIPTION) == title


def _u4_item(title: str = U4_TITLE) -> FeedItem:
    return {
        "source": "Stadt Wien – Baustellen",
        "category": "Baustelle",
        "title": title,
        "description": U4_DESCRIPTION,
        "guid": "35a2bf6293387737ed920111491ea53c3fd0c9e8f0003d6e2a69287d3ff60605",
        "link": "https://www.data.gv.at/katalog/en/dataset/baustellen-wien-verkehrsbeeintraechtigungen",
    }


def test_the_post_filter_ships_the_repaired_title() -> None:
    out = _post_filter_baustellen([_u4_item()])
    assert [i["title"] for i in out] == [f"U4: {U4_REPAIRED}"]


def test_the_post_filter_does_not_mutate_the_cached_item() -> None:
    item = _u4_item()
    _post_filter_baustellen([item])
    assert item["title"] == U4_TITLE


def test_the_repair_keeps_the_state_key() -> None:
    # first_seen and the translation cache are keyed on the guid, so the
    # repaired title neither resets the item's age nor its feed position.
    raw, repaired = _u4_item(), _u4_item(f"U4: {U4_REPAIRED}")
    assert build_feed._state_key_for_item(raw) == build_feed._state_key_for_item(repaired)
