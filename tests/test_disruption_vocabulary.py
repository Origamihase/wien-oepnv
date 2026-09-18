"""WL's disruption vocabulary, where the model's literal reading goes wrong.

These are ordinary German compounds, so Marian renders them literally and
lands somewhere between odd and alarming. Every line below is a published
translation, counted over 314 EN texts in the feed history:

===========================  ==============================  =======
German                       English as published            Items
===========================  ==============================  =======
Schadhafter Zug              "Harmful train"                       4
Schadhafter Bus              "Harmful bus"                         3
Schadhafter PKW              "Harmful car"                         2
Fremdunfall                  "Foreign accident"                    9
Falschparker                 "False Parker" / "wrong parker"       7
Verunreinigung               "Impurity" / "contamination"          4
Wasserrohrgebrechen          "Water pipe fractures"                2
Klapprampensperre            "Folding ramp lock"                   3
Verkehrsstörung              "Traffic disturbance"                 2
===========================  ==============================  =======

"Harmful train" is the worst of them: it tells an English reader the train
is dangerous rather than broken. ``Fremdunfall`` is WL's term for an
accident caused from outside the network, and "Foreign accident" reads as
one that happened abroad. ``Falschparker`` is a vehicle parked in the way;
"False Parker" reads as somebody's surname. ``Klapprampensperre`` means the
wheelchair ramps cannot be extended, which "lock" does not convey — and
that one matters to the readers least able to work around it.

Two of these already had half their family in the glossary:
``Schadhaftes Fahrzeug`` and ``Schadhafter LKW`` were covered while
``Zug``/``Bus``/``PKW`` were not, and ``Betriebsstörung`` → "service
disruption" was covered while ``Verkehrsstörung`` was not. The additions
follow the established inflection and wording rather than inventing a
second convention.

``Klapprampen`` on its own is deliberately NOT a glossary key: the model
already renders it as "folding ramps" in running prose, and only the
compound ``Klapprampensperre`` was breaking.
"""

from __future__ import annotations

import pytest

from src import build_feed
from src.build_feed import (
    _apply_domain_glossary,
    _fix_glossary_articles,
    _normalise_for_translation,
    _unmask_entities,
)


def _glossed(text: str) -> str:
    masked, mapping = _apply_domain_glossary(_normalise_for_translation(text))
    return _unmask_entities(masked, mapping)


@pytest.mark.parametrize(
    ("german", "english", "was_published_as"),
    [
        ("Schadhafter Zug", "defective train", "Harmful train"),
        ("Schadhafter Bus", "defective bus", "Harmful bus"),
        ("Schadhafter PKW", "defective car", "Harmful car"),
        ("Fremdunfall", "third-party accident", "Foreign accident"),
        ("Falschparker", "illegally parked vehicle", "False Parker"),
        ("Verunreinigung", "contamination", "Impurity"),
        ("Wasserrohrgebrechen", "burst water pipe", "Water pipe fractures"),
        ("Klapprampensperre", "folding ramps out of service", "Folding ramp lock"),
        ("Verkehrsstörung", "traffic disruption", "Traffic disturbance"),
    ],
)
def test_each_term_resolves(german: str, english: str, was_published_as: str) -> None:
    """*was_published_as* is what a subscriber actually read before this."""
    out = _glossed(german)
    assert out == english, f"{german!r} -> {out!r} (was: {was_published_as!r})"


def test_the_existing_family_members_are_untouched() -> None:
    """The additions extend a pattern; they must not shadow it.

    ``Schadhaftes Fahrzeug`` and ``Schadhafter LKW`` were already correct.
    Longest-first ordering is what keeps them that way now that more
    ``Schadhaft…`` keys share the alternation.
    """
    assert _glossed("Schadhaftes Fahrzeug") == "defective vehicle"
    assert _glossed("Schadhaftem Fahrzeug") == "defective vehicle"
    assert _glossed("Schadhafter LKW") == "defective truck"
    assert _glossed("Betriebsstörung") == "service disruption"


def test_both_inflections_resolve() -> None:
    """German case varies with the preposition, as the LKW entries show."""
    for term in ("Schadhafter Zug", "Schadhaftem Zug"):
        assert _glossed(term) == "defective train", term


def test_the_bare_ramps_noun_is_left_to_the_model() -> None:
    """Only the compound was broken, so only the compound is glossed.

    ``die Klapprampen der U-Bahnzüge können nicht ausgefahren werden``
    already comes back as "the folding ramps of the underground trains
    cannot be extended". Adding a key for it would pin a translation that
    was never wrong, for no gain.
    """
    assert "folding" not in _glossed("Klapprampen der U-Bahnzüge")


def test_the_compound_still_wins_over_its_prefix() -> None:
    """``Klapprampensperre`` must not be matched as ``Klapprampen`` + rest."""
    assert _glossed("Klapprampensperre am 18.09.2026").startswith(
        "folding ramps out of service"
    )


# ---------------- interaction with the article repair ----------------


@pytest.mark.parametrize(
    ("model_output", "expected"),
    [
        ("Due to an third-party accident", "Due to a third-party accident"),
        ("Due to an defective train", "Due to a defective train"),
        ("Due to an contamination", "Due to a contamination"),
        # Vowel-initial value: "an" is already right and must survive.
        ("Due to an illegally parked vehicle", "Due to an illegally parked vehicle"),
    ],
)
def test_the_new_values_reach_the_article_repair(
    model_output: str, expected: str
) -> None:
    """New glossary values inherit the a/an repair, in both directions.

    The repair keys off the substituted value, so every term added here is
    covered by it automatically — including the vowel-initial one, which it
    must leave alone.
    """
    mapping = {
        "XGLOnonceX0X": "third-party accident",
        "XGLOnonceX1X": "defective train",
        "XGLOnonceX2X": "contamination",
        "XGLOnonceX3X": "illegally parked vehicle",
    }
    assert _fix_glossary_articles(model_output, mapping) == expected


def test_the_new_values_keep_the_letter_rule_safe() -> None:
    """The article repair's precondition, re-checked after the additions.

    ``test_every_glossary_value_is_safe_for_the_letter_rule`` guards the
    whole glossary; this states plainly that the terms added here were
    chosen with that constraint in mind rather than tripping over it later.
    """
    for value in (
        "defective train",
        "defective bus",
        "defective car",
        "third-party accident",
        "illegally parked vehicle",
        "contamination",
        "burst water pipe",
        "folding ramps out of service",
        "traffic disruption",
    ):
        first = value[0].lower()
        assert first != "h", value
        assert not value.lower().startswith(("one", "eu")), value


def test_translation_cache_epoch_was_bumped() -> None:
    """31 published items carry one of these, all cached as successes.

    "Harmful train" is wrong without being German, so neither the
    Sticky-German guard nor the entity guard would ever evict it.
    """
    assert build_feed._TRANSLATION_CACHE_EPOCH >= 12
