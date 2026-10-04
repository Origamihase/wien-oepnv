"""EN: where WL's stop tickers say the vehicles stop (Jules audits 2026-10-02, 2026-10-04).

The English feed showed "66A: Buses stop Salvatorianerplatz" (72 of the EN
states sampled since 2026-09-01) and "52: Trains stop for lines 6 and 18";
the model picked the preposition after "Busse/Züge halten" anew in every
build ("at", none, "for", "on"). The phrases below are real WL texts from
the cache history. Only the English feed is affected (rank 3); the model is
replaced by an echo stub, because what is tested is what the glossary
renders before the model sees the text.
"""

from __future__ import annotations

from typing import Any

import pytest

from src import build_feed
from src.build_feed import (
    _apply_domain_glossary,
    _normalise_for_translation,
    _unmask_entities,
)

_WL = "Wiener Linien"


def _rendered(text: str) -> str:
    glossed, mapping = _apply_domain_glossary(
        _normalise_for_translation(text), source=_WL, category="Störung"
    )
    return _unmask_entities(glossed, mapping)


@pytest.mark.parametrize(
    ("german", "english"),
    [
        # a place right after the verb
        ("Busse halten Salvatorianerplatz", "buses stop at Salvatorianerplatz"),
        ("Züge halten Wipplingerstr 39", "trains stop at Wipplingerstr 39"),
        ("Züge halten Steig A", "trains stop at Steig A"),
        (
            "Ersatzbus hält Karl-Waldbrunner-Platz vor Schloßhofer Straße",
            "replacement bus stops at Karl-Waldbrunner-Platz vor Schloßhofer Straße",
        ),
        # "bei" and a place
        ("Züge halten bei Währinger Gürtel 164", "trains stop at Währinger Gürtel 164"),
        ("Busse halten bei Taborstraße 62", "buses stop at Taborstraße 62"),
        # "bei" and the stop of another line, with WL's spelling variants
        ("Züge halten bei Linien 6 und 18", "trains stop at the stops of lines 6 und 18"),
        ("Züge halten bei den Linien 52,60", "trains stop at the stops of lines 52,60"),
        ("Züge halten bei Linie 18, Richtung Burggasse", "trains stop at the stop of line 18, Richtung Burggasse"),
        ("Züge halten bei der Linie 74A", "trains stop at the stop of line 74A"),
        ("Züge halten Linie O", "trains stop at the stop of line O"),
        ("Züge halte bei Linie 25", "trains stop at the stop of line 25"),
        ("Züge halten bei LInie 31", "trains stop at the stop of line 31"),
        ("Busse halten bei Linie 14A", "buses stop at the stop of line 14A"),
        # the existing stop-of-line form stays as it was
        ("Busse halten bei Haltestelle N71", "buses stop at the stop of line N71"),
        # negation
        ("Züge halten nicht in der Station Hietzing", "trains do not stop in der Station Hietzing"),
    ],
)
def test_the_stop_is_rendered_with_its_preposition(german: str, english: str) -> None:
    assert _rendered(german) == english


@pytest.mark.parametrize(
    ("german", "english"),
    [
        # a preposition of its own is left to the model
        ("Züge halten in Schleife", "trains stop in the loop"),
        ("Züge halten in der Tokiostraße", "trains stop in der Tokiostraße"),
        ("Busse halten auf Hauptfahrbahn", "buses stop auf main carriageway"),
        ("Busse halten Auf der Hauptfahrbahn", "buses stop Auf der main carriageway"),
        ("Züge halten am Uhlplatz", "trains stop am Uhlplatz"),
        ("Busse halten vor Gumpendorfer Straße", "buses stop vor Gumpendorfer Straße"),
        ("Züge halten nach Bellariastraße", "trains stop nach Bellariastraße"),
        ("Züge halten gegenüber bei Linie 31", "trains stop opposite bei Linie 31"),
        ("Züge halten Gegenüber", "trains stop opposite"),
        ("Züge halten Vis a Vis", "trains stop Vis a Vis"),
        ("Busse halten in Richtung Schottenring", "buses stop in Richtung Schottenring"),
        # nothing after the verb
        ("Züge halten", "trains stop"),
    ],
)
def test_other_words_after_the_verb_are_untouched(german: str, english: str) -> None:
    assert _rendered(german) == english


def test_compounds_are_not_rewritten() -> None:
    assert _normalise_for_translation("Fernzüge halten Wien Meidling") == "Fernzüge halten Wien Meidling"


def test_the_title_reads_as_published(monkeypatch: Any) -> None:
    seen: list[str] = []

    def echo(text: str, **_kwargs: Any) -> list[dict[str, str]]:
        seen.append(text)
        return [{"translation_text": text}]

    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: echo)
    out = build_feed._translate_title_attempt("66A: Busse halten Salvatorianerplatz", source=_WL)
    assert out is not None
    assert build_feed._capitalise_title_body(out) == "66A: Buses stop at Salvatorianerplatz"
    out = build_feed._translate_title_attempt("52: Züge halten bei Linien 6 und 18", source=_WL)
    assert out is not None
    assert build_feed._capitalise_title_body(out).startswith(
        "52: Trains stop at the stops of lines 6"
    )


def test_the_cache_epoch_was_bumped() -> None:
    # Cached under 20: "66A: Buses stop Salvatorianerplatz"; the source
    # digest is unchanged, so only a bump evicts it.
    assert build_feed._TRANSLATION_CACHE_EPOCH >= 21
