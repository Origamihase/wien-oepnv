"""A declined stop name reaches the EN model in the form of the stop sign.

KI watcher, 2026-11-01: the EN feed read "Continue via line 37 to High
Wait." (line 38, 31.10.2026 18:30; "High Wait" also on 23.05.2026). WL wrote
"Weiterfahrt über die Linie 37 bis Hohen Warte"; the directory knows "Hohe
Warte", and the masker shields names only as written. ``_station_base_forms``
turns the weak form of a name that opens with an adjective back into the
directory's name before the text is masked. The rule is the grammar
(-e/-er/-es → -en), not a word list; the German feed is untouched.
"""

from __future__ import annotations

from typing import Any

import pytest

from src import build_feed
from src.build_feed import _mask_entities, _normalise_for_translation, _station_base_forms


def _echo(seen: list[str]) -> Any:
    def echo(text: str, **_kwargs: Any) -> list[dict[str, str]]:
        seen.append(text)
        return [{"translation_text": text}]

    return echo


@pytest.mark.parametrize(
    ("german", "base"),
    [
        ("Weiterfahrt über die Linie 37 bis Hohen Warte.", "Weiterfahrt über die Linie 37 bis Hohe Warte."),
        ("Busse fahren bis zum Hohen Markt.", "Busse fahren bis zum Hoher Markt."),
        ("Umleitung zur Alten Donau.", "Umleitung zur Alte Donau."),
        ("Betrieb nur bis zur Neuen Donau.", "Betrieb nur bis zur Neue Donau."),
    ],
)
def test_a_declined_stop_name_takes_the_form_of_the_directory(german: str, base: str) -> None:
    assert _station_base_forms(german) == base


@pytest.mark.parametrize(
    "german",
    [
        "Betrieb nur zwischen Hohe Warte und Nußdorfer Straße U.",
        "Wegen einer Demonstration in der Inneren Stadt kommt es zu Umleitungen.",
        "Die Hohen Warten sind gesperrt.",  # not followed by the name's noun as one word
        "Hohenwarte",
    ],
)
def test_text_without_a_declined_stop_name_stays_as_it_is(german: str) -> None:
    assert _station_base_forms(german) == german


def test_the_declined_name_is_shielded_like_the_name() -> None:
    _masked, mapping = _mask_entities(_normalise_for_translation("Weiterfahrt über die Linie 37 bis Hohen Warte."))
    assert "Hohe Warte" in mapping.values()


def test_the_model_never_sees_the_declined_name(monkeypatch: Any) -> None:
    seen: list[str] = []
    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: _echo(seen))
    english = build_feed._translate_text_attempt(
        "Betrieb nur zwischen Schottentor U und Nußdorfer Straße U. Weiterfahrt über die Linie 37 bis Hohen Warte.",
        source="Wiener Linien",
        category="Störung",
    )
    assert english is not None and "Hohe Warte" in english
    assert all("Hohen" not in text and "Warte" not in text for text in seen)


def test_a_form_the_directory_knows_as_a_name_stays() -> None:
    known = {name.casefold() for name in build_feed._station_entity_names()}
    assert all(form.casefold() not in known for form in build_feed._declined_station_names())
