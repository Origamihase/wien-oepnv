"""EN: a stop left out ("nicht einhalten") and stop names carrying "Wien".

Two classes from the EN probe with the real model of 2026-10-15, each with
the real WL / ÖBB wording it came from:

* "Die Linie 66A kann die Haltestelle Atzgersdorf, Kirchenplatz in Richtung
  Reumannplatz nicht einhalten" became "Line 66A cannot follow the stop …".
* "Kein Betrieb Wien Schedifkaplatz – Wien Matzleinsdorfer Platz" became
  "no service Vienna Schedifkaplatz – Wien Matzleinsdorfer Platz"; ÖBB's
  "Wien St.Marx Bahnhst" and "Purkersdorf b.Wien Zentrum" went the same way.

The assertions check what the model is shown.
"""

from __future__ import annotations

import pytest

from src import build_feed

_WL = "Wiener Linien"


def _model_input(german: str, source: str = _WL) -> str:
    """What the model is shown for *german* (glossary terms resolved)."""
    glossed, glossary = build_feed._apply_domain_glossary(
        build_feed._normalise_for_translation(german), source=source
    )
    masked, entities = build_feed._mask_entities(glossed)
    for placeholder, english in glossary.items():
        masked = masked.replace(placeholder, f"<{english}>")
    for placeholder, surface in entities.items():
        masked = masked.replace(placeholder, f"[{surface}]")
    return masked


@pytest.mark.parametrize(
    ("german", "shown"),
    [
        (
            "Die Linie 66A kann die Haltestelle Atzgersdorf, Kirchenplatz in Richtung "
            "Reumannplatz nicht einhalten. Grund: Polizeieinsatz.",
            "Die Linie [66A] <does not stop at> [Atzgersdorf, Kirchenplatz] in Richtung [Reumannplatz].",
        ),
        (
            "Aufgrund der Bauarbeiten werden die Busse über die Breitenseer Straße umgeleitet "
            "und können die Haltestellen Klinik Ottakring und Waidäckergasse nicht einhalten!",
            "umgeleitet und <do not stop at> [Klinik Ottakring] und [Waidäckergasse]!",
        ),
        (
            "Die Linie U6 kann die Stationen Thaliastraße und Burggasse, Stadthalle in "
            "Richtung Floridsdorf ab ca. 22:30 Uhr nicht einhalten.",
            "Die Linie [U6] <does not stop at> [Thaliastraße] und [Burggasse, Stadthalle] in Richtung",
        ),
    ],
)
def test_a_stop_left_out_does_not_stop(german: str, shown: str) -> None:
    model_input = _model_input(german)
    assert shown in model_input
    assert "einhalten" not in model_input


def test_the_station_form_with_the_subject_behind_the_verb_is_unchanged() -> None:
    # The older rule, verified with the real model ("the U6 line trains do not
    # stop at Burggasse - Stadthalle station"), keeps its own form.
    assert "halten die Züge der Linie [U6] in beiden Richtungen nicht in der Station" in _model_input(
        "Derzeit können die Züge der Linie U6 die Station Alser Straße in beiden "
        "Richtungen nicht einhalten."
    )


@pytest.mark.parametrize(
    ("german", "name"),
    [
        (
            "Badner Bahn: Kein Betrieb Wien Schedifkaplatz – Wien Matzleinsdorfer Platz",
            "[Wien Schedifkaplatz]",
        ),
        ("zwischen Wien St.Marx Bahnhst und Wien Geiselbergstraße Bahnhst", "[Wien St.Marx]"),
        (
            "zwischen Wien Hütteldorf Bahnhof (U) und Purkersdorf b.Wien Zentrum Bahnhst",
            "[Purkersdorf b.Wien Zentrum]",
        ),
        ("die Halte in Purkersdorf b. Wien Sanatorium Bahnhst", "[Purkersdorf b. Wien Sanatorium]"),
        ("zwischen Wien Meidling Bahnhof (U) und Hennersdorf b.Wien Bahnhst", "[Hennersdorf b.Wien]"),
        ("in Gerasdorf b. Wien Bahnhof", "[Gerasdorf b. Wien]"),
    ],
)
def test_wien_in_a_stop_name_stays_with_the_name(german: str, name: str) -> None:
    assert name in _model_input(german, source="ÖBB")


@pytest.mark.parametrize(
    "german",
    [
        "die Fernverkehrszüge in Fahrtrichtung Wien mit geänderten Fahrzeiten",
        "Grund: Veranstaltung im Bereich Großraum Wien.",
        "Bauarbeiten Wien Stammstrecke Phase 2",
        "Zugausfälle zwischen Linz und Wien – Ersatzverkehr",
    ],
)
def test_wien_as_the_city_is_left_to_the_model(german: str) -> None:
    model_input = _model_input(german, source="ÖBB")
    assert "Wien" in model_input
    assert "[Wien" not in model_input and "b.Wien]" not in model_input


def test_a_name_used_with_and_without_the_city_keeps_both_forms() -> None:
    text = "Kein Betrieb Wien Schedifkaplatz; Busse halten Schedifkaplatz"
    masked, entities = build_feed._mask_entities(text)
    assert sorted(entities.values()) == ["Schedifkaplatz", "Wien Schedifkaplatz"]
    assert build_feed._unmask_entities(masked, entities) == text


def test_every_placeholder_in_the_mapping_is_in_the_text() -> None:
    # A placeholder the city was folded into must not linger in the mapping:
    # the dropped-entity guard would count it as lost by the model.
    masked, entities = build_feed._mask_entities("zwischen Wien Schedifkaplatz und Gerasdorf b. Wien")
    assert all(placeholder in masked for placeholder in entities)
    assert len(entities) == 2
