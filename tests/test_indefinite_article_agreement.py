"""The model picks the article for a placeholder. Unmasking strands it.

Published 2026-09-18, in two of the ten live items at once::

    Due to an switch fault between Tullnerfeld … and Wien Meidling …
    Due to an demonstration in the area of Schwarzenbergplatz and Ring

and, in the archive, eleven times::

    After an service obstruction there are different intervals.

The model is not being careless. Every placeholder starts with ``X``, whose
letter name begins with a vowel sound, so ``an XGLO…X0X`` is exactly the
right article for the token it was shown. :func:`_unmask_entities` then
swaps in a consonant-initial English term and leaves the article behind.
Nothing downstream looks at it again.

Measured over 314 published EN texts: 15 wrong articles, three distinct
words — ``service obstruction`` (11), ``switch fault`` (3),
``demonstration`` (1) — and every one of them a glossary value. The eight
article pairs the model got right (``an event``, ``a defective``,
``an interlocking``, ``a police``, ``an obstacle``) must survive untouched.

**Why glossary values only.** The naive letter rule is safe for all 97
glossary values — the vowel-lettered ones are genuinely vowel-sounding and
none begins with a silent ``h`` — but it is not safe in general. "a U-Bahn"
and "an hour" both go the other way, and entity placeholders restore
exactly that kind of token (``U6``, ``S45``). Limiting the repair to what
the glossary itself inserted keeps the rule inside the set where it was
verified, and means prose the model wrote is never rewritten.

One more reason to stay narrow: the corpus also contains ``77 a After:``,
which looks like an article pair and is a house number the model split.
A rule that swept the text for ``\\ba\\b`` would have rewritten it.
"""

from __future__ import annotations

from typing import Any

import pytest

from src import build_feed
from src.build_feed import _agreeing_article, _fix_glossary_articles

# A mapping shaped like a real one: glossary placeholders carry English,
# entity placeholders carry the German surface verbatim.
MAPPING = {
    "XGLOnonceX0X": "switch fault",
    "XGLOnonceX1X": "demonstration",
    "XGLOnonceX2X": "service obstruction",
    "XGLOnonceX3X": "event",
    "XENTnonceX0X": "U6",
    "XENTnonceX1X": "Schwarzenbergplatz",
}


# ---------------- the live defects ----------------


@pytest.mark.parametrize(
    ("published", "expected"),
    [
        (
            "Due to an switch fault between Tullnerfeld and Wien Meidling",
            "Due to a switch fault between Tullnerfeld and Wien Meidling",
        ),
        (
            "Due to an demonstration in the area of Schwarzenbergplatz",
            "Due to a demonstration in the area of Schwarzenbergplatz",
        ),
        (
            "After an service obstruction there are different intervals.",
            "After a service obstruction there are different intervals.",
        ),
    ],
)
def test_the_live_wrong_articles_are_repaired(published: str, expected: str) -> None:
    assert _fix_glossary_articles(published, MAPPING) == expected


def test_sentence_initial_case_is_kept() -> None:
    assert _fix_glossary_articles("An switch fault occurred", MAPPING) == (
        "A switch fault occurred"
    )


def test_the_repair_works_in_both_directions() -> None:
    """``a`` before a vowel is the same defect pointing the other way."""
    assert _fix_glossary_articles("a event happened", MAPPING) == (
        "an event happened"
    )


# ---------------- what must stay untouched ----------------


@pytest.mark.parametrize(
    "correct",
    [
        "Due to an event the line is closed",
        "Due to a defective vehicle",
        "an interlocking failure",
        "a police operation",
        "an obstacle on the track",
    ],
)
def test_articles_the_model_got_right_are_left_alone(correct: str) -> None:
    """Eight such pairs in the corpus. Rewriting any of them is a new defect."""
    assert _fix_glossary_articles(correct, MAPPING) == correct


def test_an_entity_is_never_touched() -> None:
    """``U6`` is why this is restricted to glossary values.

    The letter ``U`` opens a vowel *letter* and a consonant *sound*, so the
    naive rule says "an U6" and English says "a U6". Entity placeholders
    restore exactly this kind of token, so they stay out of the repair —
    which also means a pre-existing wrong article in front of one is left
    as it is, deliberately.
    """
    assert _fix_glossary_articles("a U6 train", MAPPING) == "a U6 train"
    assert _fix_glossary_articles("an U6 train", MAPPING) == "an U6 train"


def test_a_house_number_is_not_an_article() -> None:
    """The corpus really contains ``77 a After:`` — a split house number.

    Only text immediately in front of a glossary value is considered, so a
    stray ``a`` elsewhere in the sentence is never a candidate.
    """
    text = "From: Anzengruberstraße opposite 77 a After: Hüttergasse 6A-6B"
    assert _fix_glossary_articles(text, MAPPING) == text


def test_a_word_that_merely_starts_like_a_value_is_safe() -> None:
    """``services`` is not the value ``service obstruction``."""
    text = "an services register"
    assert _fix_glossary_articles(text, MAPPING) == text


def test_an_inside_a_word_is_not_an_article() -> None:
    """What the leading ``\\b`` actually protects — and it is not obvious.

    The first probe written for this test (``an services register``) passes
    with or without the guard, so it proved nothing. The real damage is a
    word that merely *ends* in ``an`` directly before a glossary value:
    without the boundary the pattern eats those two letters and publishes
    ``Germa switch fault``.
    """
    for text in ("German switch fault", "a European switch fault"):
        assert _fix_glossary_articles(text, MAPPING) == text


def test_an_empty_mapping_changes_nothing() -> None:
    text = "Due to an switch fault"
    assert _fix_glossary_articles(text, {}) == text


# ---------------- the rule itself ----------------


@pytest.mark.parametrize(
    ("article", "following", "expected"),
    [
        ("an", "switch fault", "a"),
        ("a", "event", "an"),
        ("An", "switch fault", "A"),
        ("a", "obstacle", "an"),
        ("an", "event", "an"),
    ],
)
def test_agreeing_article(article: str, following: str, expected: str) -> None:
    assert _agreeing_article(article, following) == expected


def test_every_glossary_value_is_safe_for_the_letter_rule() -> None:
    """The claim the restriction rests on, checked against the real glossary.

    The repair uses a first-letter test. That is only defensible because no
    glossary value has a first letter whose sound disagrees with it: no
    silent ``h``, and no vowel letter opening a consonant sound (``one``,
    ``eu``, a ``u`` pronounced "you"). If a future entry breaks that, this
    fails and the entry needs an exception rather than the rule needing a
    rewrite.
    """
    from src.build_feed import (
        _GLOSSARY_BASE,
        _GLOSSARY_BY_CATEGORY,
        _GLOSSARY_BY_SOURCE,
    )

    values = set(_GLOSSARY_BASE.values())
    for layer in (*_GLOSSARY_BY_SOURCE.values(), *_GLOSSARY_BY_CATEGORY.values()):
        values |= set(layer.values())

    offenders = [
        value
        for value in values
        if value
        and (
            value[0].lower() == "h"
            or value.lower().startswith(("one", "eu"))
            or (value[0].lower() == "u" and value.lower()[:2] not in ("un", "up", "ur"))
        )
    ]
    assert offenders == [], offenders


# ---------------- the repair must actually run ----------------


def test_the_repair_is_wired_into_the_translation_path(monkeypatch: Any) -> None:
    """Isolated unit tests cannot tell whether anything calls this.

    A mutation that simply removes the call from ``_translate_text_attempt``
    left every other test in this file passing. This is the one that fails,
    so the repair cannot quietly stop running.
    """
    def model(text: str, **kwargs: Any) -> list[dict[str, str]]:
        # Marian's own behaviour: "an" is correct for the placeholder token.
        return [{"translation_text": "Due to an " + text.split()[-1]}]

    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: model)
    out = build_feed._translate_text_attempt("Wegen einer Weichenstörung")

    assert out is not None
    assert "an switch fault" not in out, out
    assert "a switch fault" in out, out


# ---------------- the fix must reach the reader ----------------


def test_translation_cache_epoch_was_bumped() -> None:
    """All 15 occurrences were cached as successes.

    "an switch fault" is wrong without being German, so the Sticky-German
    guard will not retry it and the entity guard sees nothing missing.
    """
    assert build_feed._TRANSLATION_CACHE_EPOCH >= 11
