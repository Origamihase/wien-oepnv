"""The entrance of a park or a building in a relocation's place is English.

Published 2026-11-07 to 2026-11-08 in ``docs/feed.en.xml``::

    68A: Laaer Berg, Kurpark Nordeingang
    Filmteichstraße before Nordeingang des Kurparks → approx. 30 metres …

The record is rendered without a model (``_render_record_table``), and its
word table did not know the entrance. The rule is bound to the genitive or
"zum"/"zur" behind the word, so a stop that carries it keeps its name.

Mutations checked against this file (each one caught, by the test named):

* the entrance pass is not called from ``_gloss_record_values`` →
  ``test_the_68a_place_reads_english``.
* the genitive anchor is dropped (any "Nordeingang") →
  ``test_a_stop_named_after_an_entrance_keeps_its_name``.
* the genitive ending is kept ("of Kurparks") →
  ``test_the_68a_place_reads_english``.
* only "Eingang" is a way in (2026-11-13) → ``test_the_8a_place_reads_english``.
* "Einfahrt" reads "entrance" → ``test_the_8a_place_reads_english``.
* "Zufahrt zum Campingplatz" loses its fixed wording → ``test_the_access_forms``.
"""

from __future__ import annotations

import pytest

from src.build_feed import _render_record_table

# Verbatim from cache/wl_9d709a/events.json (05.11.2026), after HTML removal.
DE_68A = (
    "Haltestelle: Laaer Berg, Kurpark Nordeingang Von: Filmteichstraße vor "
    "Nordeingang des Kurparks Nach: Etwa 30 Meter Richtung Laaer-Berg-Straße"
)


def _render(record: str) -> str:
    return _render_record_table(record, source="Wiener Linien", category="Hinweis")


def test_the_68a_place_reads_english() -> None:
    assert _render(DE_68A) == (
        "Stop: Laaer Berg, Kurpark Nordeingang From: Filmteichstraße before "
        "the north entrance of Kurpark To: Approx. 30 metres towards "
        "Laaer-Berg-Straße"
    )


def test_a_stop_named_after_an_entrance_keeps_its_name() -> None:
    rendered = _render("Von: Laaer Berg, Nordosteingang Nach: Kurpark Nordeingang 3")
    assert rendered == "From: Laaer Berg, Nordosteingang To: Kurpark Nordeingang 3"


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ("Von: Eingang des Friedhofs", "From: Entrance of Friedhof"),
        (
            "Von: Rosengasse 5 vor Haupteingang des Bahnhofes",
            "From: Rosengasse 5 before the main entrance of Bahnhof",
        ),
        (
            "Nach: Rosengasse gegenüber Seiteneingang der Stadthalle",
            "To: Rosengasse opposite the side entrance of Stadthalle",
        ),
        (
            "Nach: Rosengasse 9 nach Eingang zur Stadthalle",
            "To: Rosengasse 9 after the entrance to Stadthalle",
        ),
    ],
)
def test_the_entrance_forms(record: str, expected: str) -> None:
    assert _render(record) == expected


# Every way in or out of a place, not only the entrance on foot (KI-Wächter
# 13.11.2026, issue #2209): "8A: Küniglberg, ORF Zentrum" read "Würzburggasse
# after the driveway zum Friedhof". Verbatim from cache/wl_9d709a/events.json
# (12.11.2026), after HTML removal.
DE_8A = "Von: Würzburggasse nach Einfahrt zum Friedhof Nach: Würzburggasse 49"


def test_the_8a_place_reads_english() -> None:
    assert _render(DE_8A) == (
        "From: Würzburggasse after the driveway to Friedhof To: Würzburggasse 49"
    )
    # The relocation's way, as the summary shows it.
    assert _render("Würzburggasse nach Einfahrt zum Friedhof → Würzburggasse 49.") == (
        "Würzburggasse after the driveway to Friedhof → Würzburggasse 49."
    )


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ("Von: Rosengasse 3 vor Ausfahrt der Garage", "From: Rosengasse 3 before the exit of Garage"),
        ("Nach: Rosengasse nach Zufahrt zur Schule", "To: Rosengasse after the access road to Schule"),
        ("Von: Rosengasse vor Seitenausgang des Bades", "From: Rosengasse before the side exit of Bad"),
        ("Von: Hauseinfahrt der Stadthalle", "From: Building entrance of Stadthalle"),
        ("Von: Zugang zum Park", "From: Access to Park"),
        # The fixed wording of _RECORD_VALUE_RULES stays.
        ("Von: Zufahrt zum Campingplatz", "From: The campsite access road"),
        # Without an article behind it the word table renders the noun alone.
        ("Von: Rosengasse vor Einfahrt 5", "From: Rosengasse before the driveway 5"),
    ],
)
def test_the_access_forms(record: str, expected: str) -> None:
    assert _render(record) == expected
