"""WL's diversion wording gets one English word, "diversions".

Inside a sentence the fixed noun "diversions" left the model no noun to
build the verb on ("kommt es zu Umleitungen" → "diversions occurs", EN feed
22.–24.10.2026); left alone, the model wrote "there are redirections"
while every other WL text says "diversion". The whole phrase is fixed now
(operator wish 2026-10-24: "Der Feed soll ja mehrere Quellen
vereinheitlichen."), the bare noun only as the head of the short form.
"""

from __future__ import annotations

from src.build_feed import _apply_domain_glossary, _normalise_for_translation, _unmask_entities


def _glossed(text: str) -> str:
    """Apply the glossary and resolve it back, skipping the NMT model."""
    masked, mapping = _apply_domain_glossary(_normalise_for_translation(text))
    return _unmask_entities(masked, mapping)


def test_the_diversion_sentence_is_fixed() -> None:
    text = "Wegen Bauarbeiten im Bereich Meiereistraße / Stephanie-Endres-Straße kommt es zu Umleitungen."
    assert _glossed(text).endswith("there are diversions.")


def test_a_wl_intersection_names_its_streets() -> None:
    # The model moved "intersection" behind the fixed phrase at the sentence
    # end: "… Stephanie-Endres-Straße there are diversions intersection."
    text = "Wegen Bauarbeiten im Kreuzungsbereich Meiereistraße / Stephanie-Endres-Straße kommt es zu Umleitungen."
    masked, mapping = _apply_domain_glossary(_normalise_for_translation(text), source="Wiener Linien")
    glossed = _unmask_entities(masked, mapping)
    assert "at the intersection of Meiereistraße" in glossed
    assert "im Kreuzungsbereich" in _glossed(text)  # other sources: left to the model


def test_the_phrase_keeps_what_follows() -> None:
    assert "there are diversions der Linie 77A" in _glossed(
        "Wegen einer Veranstaltung im Ernst-Happel-Stadion kommt es zu Umleitungen der Linie 77A."
    )


def test_the_head_of_the_short_form() -> None:
    assert _glossed("Umleitungen: 36B Berzeliusgasse – Brünner Straße.").startswith("diversions: 36B")


def test_the_bare_noun_inside_a_sentence_goes_to_the_model() -> None:
    assert "Umleitungen" in _glossed("Es gibt Umleitungen in beiden Richtungen.")
