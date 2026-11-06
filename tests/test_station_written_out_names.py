"""WL's "Bhf. X" stop names stand written out as "Bahnhof X" (2026-11-06).

The stop directory abbreviates: "Wien Bhf. Meidling (WL)", "Wien Bhf.
Hütteldorf (WL)", "Wien Bhf. Meidling, Schedifkaplatz (WL)" (ten names). WL's
notices write the name out ("Haltestellenverlegung der Linie 15A in Richtung
Bahnhof Meidling S U, Schedifkaplatz", 2026-11-05), only "Meidling" was
shielded, and the English feed read "Station Meidling" (finding of the KI
watcher, 2026-11-06). The written-out and the dotless form are the same name
(``_station_written_out_forms``); a "Bahnhof" in front of a stop the
directory names without it stays running text, as every other word does.
"""

from __future__ import annotations

import pytest

from src.build_feed import _mask_entities
from src.feed_build.masking import _station_entity_names


def _unprotected(text: str) -> str:
    """The part of *text* the masker did NOT shield, placeholders as ``□``."""
    masked, mapping = _mask_entities(text)
    for placeholder in mapping:
        masked = masked.replace(placeholder, "□")
    return masked


@pytest.mark.parametrize(
    "name",
    [
        "Bahnhof Meidling",
        "Bahnhof Meidling, Schedifkaplatz",
        "Bahnhof Meidling, Eichenstraße",
        "Bhf Meidling",
        "Bahnhof Hütteldorf",
        "Bahnhof Atzgersdorf",
        "Wien Bahnhof Meidling",
    ],
)
def test_the_written_out_name_is_a_name(name: str) -> None:
    assert name in _station_entity_names()


@pytest.mark.parametrize("text", ["Bahnhof Heiligenstadt", "Bahnhof Kagran", "Bahnhof Praterstern", "Meidling Bahnhof"])
def test_a_bahnhof_in_front_of_another_stop_is_no_name(text: str) -> None:
    assert text not in _station_entity_names()


def test_the_stop_name_is_shielded_whole() -> None:
    text = "Haltestellenverlegung der Linie 15A in Richtung Bahnhof Meidling, Schedifkaplatz"
    assert _unprotected(text) == "Haltestellenverlegung der Linie □ in Richtung □"
    _masked, mapping = _mask_entities(text)
    assert "Bahnhof Meidling, Schedifkaplatz" in mapping.values()


def test_the_dotless_form_is_shielded_whole() -> None:
    assert _unprotected("Aufgelassene Haltestelle: Bhf Meidling, Schedifkaplatz") == "Aufgelassene Haltestelle: □"


def test_the_generic_word_stays_translatable() -> None:
    assert _unprotected("Fahrtbehinderung in Richtung Bahnhof Heiligenstadt") == "Fahrtbehinderung in Richtung Bahnhof □"


def test_oebb_names_keep_their_own_form() -> None:
    assert _unprotected("zwischen Wien Meidling Bahnhof und Wien Liesing Bahnhof") == "zwischen □ Bahnhof und □ Bahnhof"


def test_the_street_is_still_a_street() -> None:
    assert _unprotected("Umleitung über die Bahnhofstraße") == "Umleitung über die □"
