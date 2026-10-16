"""EN: a stop left out ("nicht einhalten") and stop names carrying "Wien".

Two classes from the EN probe with the real model of 2026-10-15, each with
the real WL / ÖBB wording it came from:

* "Die Linie 66A kann die Haltestelle Atzgersdorf, Kirchenplatz in Richtung
  Reumannplatz nicht einhalten" became "Line 66A cannot follow the stop …".
  "Derzeit können die Züge der Linie U6 die Station … nicht einhalten" got
  the same verb on 16.10., with the subject put in front of it.
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


@pytest.mark.parametrize(
    ("german", "shown"),
    [
        (
            "Derzeit können die Züge der Linie U6 die Station Alser Straße in beiden "
            "Richtungen nicht einhalten.",
            "Derzeit die Züge der Linie [U6] <do not stop at> [Alser Straße] in beiden Richtungen.",
        ),
        (
            "Derzeit können die Züge der Linie U6 die Station Burggasse - Stadthalle in "
            "Richtung Floridsdorf nicht einhalten.",
            "Derzeit die Züge der Linie [U6] <do not stop at> [Burggasse - Stadthalle] "
            "in Richtung [Floridsdorf].",
        ),
        (
            "Wegen eines Rettungseinsatzes kann die Linie U6 die Station Gumpendorfer "
            "Straße in Richtung Floridsdorf nicht einhalten.",
            "die Linie [U6] <does not stop at> [Gumpendorfer Straße] in Richtung [Floridsdorf].",
        ),
        (
            "Derzeit kann die Linie 13A die Haltestelle Neubaugasse in Richtung "
            "Hauptbahnhof nicht einhalten.",
            "Derzeit die Linie [13A] <does not stop at> [Neubaugasse] in Richtung",
        ),
    ],
)
def test_a_subject_behind_the_verb_goes_in_front_of_it(german: str, shown: str) -> None:
    # 16.10.2026: with the subject between verb and place nothing was glossed,
    # and the model's English changed with the nonce of the second pass.
    model_input = _model_input(german)
    assert shown in model_input
    assert "einhalten" not in model_input


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


@pytest.mark.parametrize(
    ("german", "name"),
    [
        (
            "Grund: Polizeieinsatz im Haltestellenbereich Burggasse - Stadthalle.",
            "[Burggasse - Stadthalle]",
        ),
        (
            "Die Linie U6 fährt derzeit nicht zwischen Michelbeuern - AKH und "
            "Josefstädter Straße.",
            "[Michelbeuern - AKH]",
        ),
        ("Fremdunfall Umleitung bis Julius - Raab - Platz über Linie 2", "[Julius - Raab - Platz]"),
    ],
)
def test_a_hyphen_with_spaces_stays_inside_the_name(german: str, name: str) -> None:
    # 16.10.2026: only "Burggasse" was protected, the model wrote
    # "Burggasse - City Hall".
    assert name in _model_input(german)


def _ph(kind: str, index: int) -> str:
    return f"X{kind}{build_feed._PLACEHOLDER_NONCE}X{index}X"


def test_placeholders_the_model_glued_get_their_space_back() -> None:
    line, verb, stop = _ph("ENT", 2), _ph("GLO", 0), _ph("ENT", 0)
    masked = f"Derzeit die Linie {line} {verb} {stop} in Richtung"
    glued = f"Currently line {line}{verb}{stop} towards"
    assert build_feed._respace_glued_placeholders(glued, masked) == (
        f"Currently line {line} {verb} {stop} towards"
    )


def test_placeholders_that_stand_together_in_the_source_stay_together() -> None:
    first, dash, last = _ph("ENT", 3), _ph("ENT", 4), _ph("ENT", 5)
    masked = f"Löwengasse {first}{dash}{last}"
    assert build_feed._respace_glued_placeholders(
        f"Löwengasse {first}{dash}{last}", masked
    ) == f"Löwengasse {first}{dash}{last}"
