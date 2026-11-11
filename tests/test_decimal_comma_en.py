"""A German decimal comma becomes a point before the English translation.

The Stammstrecke text read "Durchschnittliche Verspätung von 24.2 min" on
11.11.2026 (KI-Wächter, issue #2200); it now writes "24,2 min". English
takes a point, set before the model sees the text.
"""

from __future__ import annotations

import pytest

from src.feed_build.pretranslate import _normalise_for_translation


@pytest.mark.parametrize(
    ("german", "expected"),
    [
        ("Durchschnittliche Verspätung von 24,2 min in Richtung Meidling",
         "Durchschnittliche Verspätung von 24.2 min in Richtung Meidling"),
        ("1,5 km Umweg", "1.5 km Umweg"),
        ("rund 12,5 % weniger", "rund 12.5 % weniger"),
        ("3,5 m breit", "3.5 m breit"),
        ("etwa 2,5 Minuten", "etwa 2.5 Minuten"),
    ],
)
def test_decimal_comma_with_a_unit_becomes_a_point(german: str, expected: str) -> None:
    assert _normalise_for_translation(german) == expected


@pytest.mark.parametrize(
    ("german", "kept"),
    [
        ("Busse der Linien 1,2 und 71 fahren", "1,2 und 71"),
        ("U1,U2", "U1,U2"),
        ("Gleis 1,2", "Gleis 1,2"),
        ("Linien 12, 13 min", "12, 13 min"),
        ("Linien 1,2,3 min", "1,2,3 min"),
    ],
)
def test_a_comma_without_a_unit_or_in_a_list_stays(german: str, kept: str) -> None:
    assert kept in _normalise_for_translation(german)
