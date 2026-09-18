"""WL publishes stop relocations as a label block, not as prose.

::

    Haltestelle: Kraftwerk Simmering
    Von: 1. Haidequerstraße 2
    Nach: 1. Haidequerstraße 510
    Dauer: Ab …

Marian has only seen everyday German, where ``Nach`` is overwhelmingly
temporal. Published 2026-09-18::

    DE: Von: Anzengruberstraße gegenüber 77a  Nach: Hüttergasse 6A-6B
    EN: From: Anzengruberstraße opposite 77 a  After: Hüttergasse 6A-6B

A correct ``From:`` beside an ``After:`` — a relocation with an origin and
no destination. ``Dauer:`` on its own came back as "expected duration:",
borrowing the qualifier from the neighbouring ``voraussichtliche Dauer``
entry and asserting something the source never said. And the term the block
opens with had four English faces across the published history: "Stop
change", "Station relocation", "Stop transfer", and twice not translated at
all.

**Why a global glossary can hold these at all.** ``Von`` and ``Nach`` are
ordinary prepositions; keyed bare they would rewrite "Umleitung nach
Hofmühlgasse" and "Von Montag bis Freitag" into nonsense. The trailing
colon is what makes them safe — the glossary pattern ``re.escape``s each
key and closes with ``(?!\\w)``, which a colon satisfies, so the entry
matches only where the word is a field label. The negative half of this
file is what keeps that true.

Scope is what a subscriber actually sees: these six labels are the ones
attested across 154 distinct published items. The roadworks source also
emits ``Beginn:`` / ``Maßnahme:`` / ``Bezirk:``, but those sit past the
description truncation and reach no published item, so their English is
unverified and they are deliberately absent.
"""

from __future__ import annotations

import pytest

from src import build_feed
from src.build_feed import (
    _apply_domain_glossary,
    _normalise_for_translation,
    _unmask_entities,
)


def _glossed(text: str) -> str:
    """Apply the glossary and resolve it back, skipping the NMT model."""
    masked, mapping = _apply_domain_glossary(_normalise_for_translation(text))
    return _unmask_entities(masked, mapping)


# ---------------- the labels ----------------


@pytest.mark.parametrize(
    ("german", "english"),
    [
        ("Nach:", "To:"),
        ("Von:", "From:"),
        ("Haltestelle:", "Stop:"),
        ("Haltestellen:", "Stops:"),
        ("Dauer:", "Duration:"),
        ("Zeitraum:", "Period:"),
        ("Grund:", "Reason:"),
    ],
)
def test_each_label_resolves(german: str, english: str) -> None:
    assert _glossed(f"{german} Kraftwerk Simmering").startswith(english)


def test_the_live_relocation_block() -> None:
    """The 72A item, verbatim, with only the glossary applied."""
    out = _glossed(
        "Haltestellenverlegung der Linie 72A in Richtung Hasenleitengasse "
        "Haltestelle: Kraftwerk Simmering Von: 1. Haidequerstraße 2 "
        "Nach: 1. Haidequerstraße 510 Dauer: Ab"
    )

    assert "Stop: Kraftwerk Simmering" in out
    assert "From: 1. Haidequerstraße 2" in out
    assert "To: 1. Haidequerstraße 510" in out
    assert "Duration: Ab" in out
    # The defect this replaces.
    assert "After:" not in out


def test_the_from_to_pair_is_coherent() -> None:
    """The point of the fix is the *pair*, not either label alone.

    ``From: A / After: B`` states an origin and then a time. A relocation
    needs an origin and a destination, and a reader who acts on the wrong
    one walks to the wrong place.
    """
    out = _glossed("Von: Anzengruberstraße gegenüber 77a Nach: Hüttergasse 6A-6B")

    assert "From: Anzengruberstraße" in out
    assert "To: Hüttergasse" in out


def test_haltestellenverlegung_has_one_english_face() -> None:
    """One WL term, one rendering — the glossary's whole job."""
    assert _glossed("Haltestellenverlegung der Linie 72A").startswith(
        "stop relocation"
    )
    assert _glossed("Haltestellenverlegungen der Linien 12A und 14A").startswith(
        "stop relocations"
    )


# ---------------- the negative half ----------------


@pytest.mark.parametrize(
    "phrase",
    [
        "Umleitung nach Hofmühlgasse",
        "Nach Ende der Bauarbeiten",
        "Von Montag bis Freitag",
        "Der Zug fährt nach Floridsdorf",
        "Von hier an gesperrt",
    ],
)
def test_a_bare_preposition_is_never_touched(phrase: str) -> None:
    """Without the colon these keys would wreck ordinary German.

    This is the test to read first if someone ever "simplifies" the keys by
    dropping the colon: every phrase here would start carrying a field
    label in the middle of a sentence.
    """
    out = _glossed(phrase)
    assert "To:" not in out and "From:" not in out, out
    # The preposition itself survives for the model to translate in context.
    assert ("nach" in out.lower()) or ("von" in out.lower()), out


def test_a_label_only_matches_with_its_colon() -> None:
    """``Haltestelle`` as a plain noun stays the model's business.

    It is a common word in disruption prose ("die Haltestelle wird
    verlegt"), where "Stop:" would be gibberish.
    """
    out = _glossed("Die Haltestelle wird verlegt")
    assert "Stop:" not in out, out


def test_the_plural_label_is_not_eaten_by_the_singular() -> None:
    """Longest-first ordering, checked where it actually bites."""
    assert _glossed("Haltestellen: Hütteldorf, Ottakring").startswith("Stops:")


def test_the_qualified_duration_still_wins() -> None:
    """``voraussichtliche Dauer`` must beat the bare ``Dauer:``.

    Both are in the alternation; longest-first is what keeps the qualified
    form intact instead of leaving a stray "voraussichtliche" in front of
    "Duration:".
    """
    out = _glossed("voraussichtliche Dauer: 2 Stunden")
    assert "expected duration" in out
    assert "voraussichtliche" not in out


# ---------------- the fix must reach the reader ----------------


def test_translation_cache_epoch_was_bumped() -> None:
    """Cached English outlives the fix unless the epoch moves.

    Every affected item was cached as a success — "After:" is wrong without
    being German, so the Sticky-German guard will not retry it. The 72A
    relocation runs until 18.09.2027.
    """
    assert build_feed._TRANSLATION_CACHE_EPOCH >= 9
