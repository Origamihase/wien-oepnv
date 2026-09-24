"""The values of a label record are English, without a model.

Published 2026-09-24 in ``docs/feed.en.xml``, three of the ten items::

    Duration: Ab 30. September 2026, etwa 13:00, bis expected Mai 2027
    To: etwa 20 Meter in Richtung Eduard-Kittenberger-Gasse
    From: Brünnerstraße opposite 262 Ersatzlos Discontinued

The record is kept away from the model on purpose (see
``test_label_record_split.py``): labels and nouns come from the glossary,
and everything else was carried verbatim. Measured over 557 distinct WL
items (60 with a record, 40 of them with ``Dauer:``), the values follow a
small grammar — a date preposition, a day, a month, "etwa", a clock time,
one of four open-end phrases — which ``_gloss_record_values`` renders
deterministically.

The rules are record-only. "ab", "bis", "vor", "nach" are ordinary German
in prose, so they must not enter the global glossary; and inside a record
the date prepositions are bound to the digit that follows them, so "Am
Schöpfwerk" stays a stop name.

Mutations checked against this file (each one caught, by the test named):

* the record-value pass is not called from ``_render_label_record`` →
  ``test_the_live_records_render_in_english``.
* the capital at the start of a value is dropped (always lowercase) →
  ``test_the_live_records_render_in_english`` (``Duration: From``).
* the digit anchor is removed from the ``am`` rule →
  ``test_a_stop_name_starting_with_am_is_not_a_date``.
* the clock-time exclusion is removed from the house-number range →
  ``test_a_clock_time_range_is_not_dashed``.
* the rules are added to the global glossary instead →
  ``test_the_prose_path_does_not_carry_the_record_rules``.
"""

from __future__ import annotations

import pytest

from src import build_feed
from src.build_feed import (
    _apply_domain_glossary,
    _render_label_record,
    _split_label_record,
    _unmask_entities,
)

# Verbatim from cache/wl_9d709a/events.json, after HTML removal.
DE_N31 = (
    "Haltestelle: Stammersdorf Von: Brünnerstraße gegenüber 262 Ersatzlos "
    "aufgelassen Dauer: Ab 30. September 2026, etwa 13:00 Uhr, bis "
    "voraussichtlich Mai 2027 Grund: Rohrleitungsarbeiten"
)
DE_64A = (
    "Haltestelle: An den Steinfeldern Von: Perfektastraße 89 Nach: etwa 20 "
    "Meter in Richtung Eduard-Kittenberger-Gasse Dauer: Ab 21. September "
    "2026, etwa 09:00 Uhr, Dauer unbekannt Grund: Straßenbauarbeiten"
)
DE_72A = (
    "Haltestelle: Kraftwerk Simmering Von: 1. Haidequerstraße 2 Nach: 1. "
    "Haidequerstraße 510 Dauer: Ab 22. September 2026, etwa 08:00 Uhr bis "
    "voraussichtlich Ende Oktober 2027 Grund: Bauarbeiten"
)


def _render(record: str) -> str:
    return _render_label_record(record, source="Wiener Linien", category="Hinweis")


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        (
            DE_N31,
            "Stop: Stammersdorf From: Brünnerstraße opposite 262 closed without "
            "replacement Duration: From 30 September 2026, approx. 13:00, until "
            "expected May 2027 Reason: pipeline works",
        ),
        (
            DE_64A,
            "Stop: An den Steinfeldern From: Perfektastraße 89 To: Approx. 20 "
            "metres towards Eduard-Kittenberger-Gasse Duration: From 21 September "
            "2026, approx. 09:00, duration unknown Reason: roadworks",
        ),
        (
            DE_72A,
            "Stop: Kraftwerk Simmering From: 1. Haidequerstraße 2 To: 1. "
            "Haidequerstraße 510 Duration: From 22 September 2026, approx. 08:00 "
            "until expected end of October 2027 Reason: construction works",
        ),
    ],
)
def test_the_live_records_render_in_english(record: str, expected: str) -> None:
    assert _render(record) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Nicht absehbar.", "Not foreseeable."),
        ("Ab 3. Juli 2026 bis auf Widerruf", "From 3 July 2026 until further notice"),
        (
            "ab 5. August 2026, etwa 07:00 Uhr, auf derzeit unbestimmte Zeit",
            "From 5 August 2026, approx. 07:00, until further notice",
        ),
        (
            "Am 27. September 2026, von etwa 09:00 Uhr bis etwa 21:30 Uhr",
            "On 27 September 2026, from approx. 09:00 until approx. 21:30",
        ),
        ("Ab 1. Juli 2026, Dauer derzeit unbekannt", "From 1 July 2026, duration currently unknown"),
        ("Bis 30.9.2026 Betriebsschluss.", "Until 30.9.2026 end of service."),
        ("Ende 2026.", "End of 2026."),
        # The truncated live shape: the ellipsis counts like a digit.
        ("Ab …", "From …"),
        ("12. März 2026, ca. 06:00 Uhr", "12 March 2026, approx. 06:00"),
    ],
)
def test_duration_shapes(value: str, expected: str) -> None:
    assert _render(f"Dauer: {value} Grund: Bauarbeiten") == (
        f"Duration: {expected} Reason: construction works"
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "Von: Eipeldauer Straße vor Wagramer Straße Nach: Eipeldauer Straße 12 bis 14",
            "From: Eipeldauer Straße before Wagramer Straße To: Eipeldauer Straße 12-14",
        ),
        (
            "Von: Laxenburger Straße 104 nach Raxstraße "
            "Nach: Laxenburger Straße 104, Nebenfahrbahn nach Raxstraße",
            "From: Laxenburger Straße 104 after Raxstraße "
            "To: Laxenburger Straße 104, service road after Raxstraße",
        ),
        (
            "Von: Julius-Ficker-Straße gegenüber 3 Nach: Julius-Ficker-Straße 5 im Zuge Laxenburger Straße",
            "From: Julius-Ficker-Straße opposite 3 To: Julius-Ficker-Straße 5 along Laxenburger Straße",
        ),
    ],
)
def test_from_to_shapes(value: str, expected: str) -> None:
    assert _render(value) == expected


def test_a_stop_name_starting_with_am_is_not_a_date() -> None:
    out = _render("Haltestelle: Am Schöpfwerk Von: Am Schöpfwerk 3 Dauer: Am 27. September 2026")
    assert "Stop: Am Schöpfwerk From: Am Schöpfwerk 3" in out
    assert out.endswith("Duration: On 27 September 2026")


def test_a_clock_time_range_is_not_dashed() -> None:
    out = _render("Dauer: 09:00 bis 21:30 Grund: Bauarbeiten")
    assert out == "Duration: 09:00 until 21:30 Reason: construction works"


def test_the_label_itself_is_never_a_value_word() -> None:
    # "Nach:" is a label and must resolve to "To:", not to "after:".
    out = _render("Von: Perfektastraße 89 Nach: Perfektastraße 91")
    assert out == "From: Perfektastraße 89 To: Perfektastraße 91"


def test_the_ordinal_of_a_street_keeps_its_period() -> None:
    out = _render("Von: 1. Haidequerstraße 2 Nach: 1. Haidequerstraße 510")
    assert "1. Haidequerstraße 2" in out and "1. Haidequerstraße 510" in out


def test_a_rendered_record_is_deterministic_and_english() -> None:
    _, record = _split_label_record("Haltestellenverlegung der Linie 64A in Richtung X " + DE_64A)
    assert _render(record) == _render(record)
    for german in ("Ab ", "etwa", "Meter", "Richtung", "unbekannt", "Uhr", "Ersatzlos"):
        assert german not in _render(record), german


# ---------------- scope: the rules stay out of prose ----------------


def _through_global_glossary(text: str) -> str:
    glossed, mapping = _apply_domain_glossary(text, source="Wiener Linien", category="Störung")
    return _unmask_entities(glossed, mapping)


def test_the_prose_path_does_not_carry_the_record_rules() -> None:
    # "ab", "bis", "vor", "nach", "etwa" and the month names are ordinary
    # German in a sentence; the global glossary must leave them to the model
    # (other words of the sentence may be glossed — that is not the point).
    out = _through_global_glossary("Fahrt ab Hütteldorf bis etwa 21:30, vor der Station nach Mai.")
    for word in ("ab", "bis", "etwa", "vor", "nach", "Mai"):
        assert f" {word}" in out, (word, out)
    for english in ("from", "until", "approx.", "before", "after", "May"):
        assert english not in out, (english, out)


def test_the_two_phrases_did_enter_the_global_glossary() -> None:
    assert _through_global_glossary("N31: Stammersdorf ersatzlos aufgelassen") == (
        "N31: Stammersdorf closed without replacement"
    )
    assert _through_global_glossary("bis Betriebsschluss") == "bis end of service"


def test_translation_cache_epoch_was_bumped() -> None:
    assert build_feed._TRANSLATION_CACHE_EPOCH >= 16
