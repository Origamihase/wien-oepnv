"""A name keeps every Latin letter it is written with.

KI-Wächter 03.11.2026 (issue #2141), feed of 02.11.2026 14:30 Wiener Zeit::

    DE: Grund: Verkehrsstörung im Bereich Biróstraße 17.
    EN: Reason: traffic disruption in the area of Biró Street 17.

The street patterns were written with the German letters only
(``[A-Za-zÄÖÜäöüß]``), so the ``ó`` ended the name before the suffix and the
street went through the model unshielded. The same letter class sat in every
name pattern of the EN masker and in WL's address mask before the line
detection, where the house number of such a street became a phantom line
(``Biróstraße 17`` → line ``17``). :mod:`src.utils.name_letters` widens them
all at once; these tests hold the class, not the one street.
"""

from __future__ import annotations

import pytest

from src.build_feed import _mask_entities, _unmask_entities
from src.providers.wl_lines import _detect_line_pairs_from_text
from src.utils.name_letters import NAME_LETTER, NAME_LOWER, NAME_UPPER, latin_names


def _unprotected(text: str) -> str:
    """The part of *text* the masker did NOT shield, placeholders as ``□``."""
    masked, mapping = _mask_entities(text)
    for placeholder in mapping:
        masked = masked.replace(placeholder, "□")
    return masked


def test_the_letter_classes_hold_the_german_and_the_other_latin_letters() -> None:
    for letter in "ÄÖÜÉÓÁŠČŘŐ":
        assert letter in NAME_UPPER and letter not in NAME_LOWER
    for letter in "äöüßéóáščřő":
        assert letter in NAME_LOWER and letter not in NAME_UPPER
    assert "×" not in NAME_LETTER and "÷" not in NAME_LETTER
    assert latin_names("[A-ZÄÖÜ][a-zäöüß]+[A-Za-zÄÖÜäöüß-]") == (
        "[" + NAME_UPPER + "][" + NAME_LOWER + "]+[" + NAME_LETTER + "-]"
    )
    # Literal words in a pattern stay as they are.
    assert latin_names("Straße|Gürtel") == "Straße|Gürtel"


@pytest.mark.parametrize(
    ("phrase", "name"),
    [
        # The live breakage, as WL wrote it.
        ("Grund: Verkehrsstörung im Bereich Biróstraße 17.", "Biróstraße"),
        # Real Viennese streets and stops with such a letter.
        ("Rosenhügelstraße von Bertégasse bis Karl-Schwed-Gasse", "Bertégasse"),
        ("Grund: Falschparker im Bereich Esterházygasse 12.", "Esterházygasse"),
        ("Umleitung über Blériotgasse.", "Blériotgasse"),
        ("Grund: Verkehrsunfall im Bereich Dvořákgasse 3.", "Dvořákgasse"),
        # Genitive and abbreviation branches.
        ("Sperre des Linnéplatzes", "Linnéplatzes"),
        ("Umleitung ab Biróstr. 5", "Biróstr."),
        # Spaced and preposition names.
        ("Betrieb ab Béla Bartók Platz", "Béla Bartók Platz"),
        ("Falschparker im Bereich Am Fasanengarten", "Am Fasanengarten"),
        # The word after "Richtung" and ÖBB's short main station form.
        ("Fahrtbehinderung in Richtung Győr", "Győr"),
        ("Ersatzverkehr ab Košice Hbf", "Košice Hbf"),
    ],
)
def test_a_name_with_any_latin_letter_is_shielded(phrase: str, name: str) -> None:
    masked, mapping = _mask_entities(phrase)
    assert name in mapping.values(), f"{name!r} leaks: {_unprotected(phrase)!r}"
    assert _unmask_entities(masked, mapping) == phrase


@pytest.mark.parametrize(
    ("phrase", "name"),
    [
        ("Betrieb ab Parlament/ Volksthéater.", "Volksthéater"),
        ("Pamhagen/Fertőszentmiklós", "Fertőszentmiklós"),
    ],
)
def test_the_unknown_half_of_a_slash_name_keeps_its_letters(phrase: str, name: str) -> None:
    masked, mapping = _mask_entities(phrase)
    assert name in mapping.values(), f"{name!r} leaks: {_unprotected(phrase)!r}"
    assert _unmask_entities(masked, mapping) == phrase


@pytest.mark.parametrize(
    "phrase",
    [
        "Wegen eines Cafés im Bereich ist die Haltestelle verlegt.",
        "Das Café hat geöffnet.",
    ],
)
def test_an_ordinary_word_with_such_a_letter_stays_with_the_model(phrase: str) -> None:
    masked, mapping = _mask_entities(phrase)
    assert not any("Caf" in value for value in mapping.values())
    assert _unmask_entities(masked, mapping) == phrase


@pytest.mark.parametrize(
    ("text", "lines"),
    [
        ("Linie 17A: Fahrtbehinderung. Grund: Verkehrsstörung im Bereich Biróstraße 17.", ["17A"]),
        ("Verkehrsunfall im Bereich Esterházygasse 12", []),
        ("Umleitung über Dvořák Gasse 40", []),
    ],
)
def test_the_house_number_of_such_a_street_is_no_line(text: str, lines: list[str]) -> None:
    assert [token for token, _ in _detect_line_pairs_from_text(text)] == lines
