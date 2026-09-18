"""A translation may lose fluency. It may not lose a fact.

Two items published on 2026-09-18 (``docs/feed.xml`` / ``docs/feed.en.xml``)
that both passed every guard the pipeline had:

**A line that does not exist.**::

    DE: Haltestellenverlegung der Linien 12A und 14A in Richtung Schmelz …
    EN: Stop transfer of lines 12A and 14AX towards Schmelz …

The model returned the ``14A`` placeholder with its closing ``X`` doubled.
``_UNMASK_PLACEHOLDER_RE`` matched the valid prefix, restored ``14A``, and
left the surplus character glued on. ``_RESIDUAL_PLACEHOLDER_RE`` never saw
it — a bare ``X`` has neither prefix nor index.

**An address reversed.**::

    DE: Von: 1. Haidequerstraße 2     Nach: 1. Haidequerstraße 510
    EN: From: 1. Haidequerstraße 510  Duration: From 1. Haidequerstraße 510 …

The model dropped the placeholder holding ``2`` and looped on the rest. The
English does not merely omit the origin, it promotes the destination into
its place: a reader is told the stop is moving *away from* 510. For a
relocation of roughly 500 house numbers that is the difference between a
short walk and a long one in the wrong direction.

Both survived because ``_unmask_entities`` is documented as "tolerant of the
translator dropping or reordering placeholders". That tolerance is correct
for the *output* — no raw sentinel may reach a subscriber — and wrong as a
*verdict*: a dropped placeholder is a dropped fact, and what remains reads
as authoritative.

The two defects want opposite treatment, which is why this file pins both
halves:

* the doubled ``X`` is **repaired**, because the placeholder arrived intact
  and the entity is recoverable — rejecting a sound sentence over one stray
  character would be a worse trade;
* the dropped entity is **rejected**, because nothing can recover it, and
  the existing residual-sentinel guard already establishes the verdict and
  the fallback (return ``None`` → serve the German source).
"""

from __future__ import annotations

from typing import Any

import pytest

from src import build_feed
from src.build_feed import (
    _entities_dropped_by_translation,
    _mask_entities,
    _normalise_placeholder_debris,
    _unmask_entities,
)

# The two live sources, verbatim.
DE_LINES = "Haltestellenverlegung der Linien 12A und 14A in Richtung Schmelz"
DE_ADDRESS = (
    "Haltestelle: Kraftwerk Simmering Von: 1. Haidequerstraße 2 "
    "Nach: 1. Haidequerstraße 510 Dauer: Ab"
)


def _placeholder_for(mapping: dict[str, str], surface: str) -> str:
    return next(k for k, v in mapping.items() if v == surface)


# ---------------- repaired: the doubled closing X ----------------


def test_the_14ax_leak_is_repaired_not_shipped() -> None:
    """The exact live regression, reconstructed from the published strings."""
    masked, mapping = _mask_entities(DE_LINES)
    ph = _placeholder_for(mapping, "14A")

    # What the model returned: the placeholder with its closing X doubled.
    model_output = masked.replace(ph, ph + "X")

    assert _unmask_entities(model_output, mapping) == DE_LINES
    assert "14AX" not in _unmask_entities(model_output, mapping)


@pytest.mark.parametrize("surplus", ["X", "XX", "XXX"])
def test_any_run_of_surplus_x_is_absorbed(surplus: str) -> None:
    masked, mapping = _mask_entities(DE_LINES)
    ph = _placeholder_for(mapping, "14A")
    out = _unmask_entities(masked.replace(ph, ph + surplus), mapping)
    assert out == DE_LINES, out


def test_repair_does_not_reject_the_translation() -> None:
    """A recoverable placeholder must not cost the whole English sentence.

    The entity survived; only a stray character rode along. Routing this
    through the drop guard would fall back to German for a translation that
    is entirely sound.
    """
    masked, mapping = _mask_entities(DE_LINES)
    ph = _placeholder_for(mapping, "14A")
    repaired = _normalise_placeholder_debris(masked.replace(ph, ph + "X"))

    assert _entities_dropped_by_translation(masked, repaired, mapping) == []


def test_two_adjacent_placeholders_are_left_alone() -> None:
    """The one way this repair could eat real data.

    Placeholders can end up glued together, and the next one *starts* with
    ``X``. Absorbing greedily would swallow it and destroy that entity. The
    trailing run is therefore only absorbed when a non-alphanumeric follows.
    """
    masked, mapping = _mask_entities(DE_LINES)
    glued = _placeholder_for(mapping, "12A") + _placeholder_for(mapping, "14A")

    assert _normalise_placeholder_debris(glued) == glued
    assert _unmask_entities(glued, mapping) == "12A14A"


def test_the_repair_is_idempotent() -> None:
    """It runs on the translation path and again inside the unmasker."""
    masked, mapping = _mask_entities(DE_LINES)
    ph = _placeholder_for(mapping, "14A")
    once = _normalise_placeholder_debris(masked.replace(ph, ph + "X"))

    assert _normalise_placeholder_debris(once) == once


# ---------------- rejected: the dropped entity ----------------


def test_the_dropped_house_number_is_caught() -> None:
    """The reversed address, reconstructed from the published strings."""
    masked, mapping = _mask_entities(DE_ADDRESS)
    ph = _placeholder_for(mapping, "2")

    assert _entities_dropped_by_translation(
        masked, masked.replace(ph, ""), mapping
    ) == ["2"]


def test_a_dropped_street_name_is_caught() -> None:
    """The third live case: ``36A/36B`` lost ``Justgasse`` for 40 builds."""
    masked, mapping = _mask_entities("Umleitung über Justgasse und Ruthnergasse")
    ph = _placeholder_for(mapping, "Justgasse")

    assert "Justgasse" in _entities_dropped_by_translation(
        masked, masked.replace(ph, ""), mapping
    )


def test_a_clean_translation_is_not_rejected() -> None:
    masked, mapping = _mask_entities(DE_ADDRESS)
    assert _entities_dropped_by_translation(masked, masked, mapping) == []


def test_reordering_is_allowed() -> None:
    """Word order is the translator's business; the entity set is not.

    German and English disagree about where things go in a sentence, so a
    check that demanded position would reject nearly every good translation.
    Only presence is required.
    """
    masked, mapping = _mask_entities(DE_LINES)
    reversed_text = " ".join(reversed(masked.split()))

    assert _entities_dropped_by_translation(masked, reversed_text, mapping) == []


def test_a_duplicated_entity_is_not_rejected() -> None:
    """Repetition is ugly, not false — and the count check only guards loss.

    The live address item looped as well as dropped, but the loop alone
    states nothing untrue. Rejecting on it would trade a readable defect for
    a German one.
    """
    masked, mapping = _mask_entities(DE_LINES)
    ph = _placeholder_for(mapping, "14A")

    assert _entities_dropped_by_translation(
        masked, masked.replace(ph, ph + " " + ph), mapping
    ) == []


# ---------------- calibration: what may be dropped ----------------


@pytest.mark.parametrize("symbol", ["…", "–"])
def test_a_dropped_punctuation_mask_is_tolerated(symbol: str) -> None:
    """The calibration that makes the check usable rather than ignored.

    Over 400 published DE/EN pairs, demanding that *every* mask survive
    fires on 12.5% of them — and 21 of those losses are nothing but a
    dropped ellipsis or dash. Restricted to word-bearing surfaces it fires
    on 7.2%, which is exactly the three genuinely broken items. A guard that
    cried wolf on a missing ``…`` would be switched off within a week.
    """
    masked, mapping = _mask_entities(f"Betrieb ab Schwedenplatz {symbol}")
    phs = [k for k, v in mapping.items() if v == symbol]
    if not phs:
        pytest.skip(f"{symbol!r} is not entity-masked in this text")

    assert _entities_dropped_by_translation(
        masked, masked.replace(phs[0], ""), mapping
    ) == []


def test_glossary_placeholders_are_exempt() -> None:
    """``XGLO`` maps German to *different* English — absence proves nothing.

    Requiring a glossary surface to survive would reject every translation
    that did its job, since the whole point is that ``Gleisbauarbeiten``
    comes back as ``track construction works``.
    """
    mapping = {"XGLOdeadbeefX0X": "track construction works"}
    assert _entities_dropped_by_translation("XGLOdeadbeefX0X", "", mapping) == []


# ---------------- the verdict reaches the pipeline ----------------


def test_a_dropping_translation_falls_back_to_german(monkeypatch: Any) -> None:
    """End to end: the wrong-address item must not reach a subscriber.

    Same contract as the residual-sentinel guard beside it — discard, do not
    cache, serve the source. German is a visible shortcoming; a confident
    English sentence naming the wrong address is a false one.
    """
    def dropping_pipeline(text: str, **kwargs: Any) -> list[dict[str, str]]:
        # Model echoes its input but swallows the first entity placeholder.
        import re

        return [{"translation_text": re.sub(r"XENT\w+?X\d+X", "", text, count=1)}]

    monkeypatch.setattr(
        build_feed, "_get_translation_pipeline", lambda: dropping_pipeline
    )

    assert build_feed._translate_text_attempt(DE_ADDRESS) is None
    # The single-string API converts that into the untouched German source.
    assert build_feed._translate_text(DE_ADDRESS) == DE_ADDRESS


def test_a_debris_translation_still_succeeds(monkeypatch: Any) -> None:
    """The other half: a repairable output must NOT fall back."""
    def debris_pipeline(text: str, **kwargs: Any) -> list[dict[str, str]]:
        import re

        return [{"translation_text": re.sub(r"(XENT\w+?X\d+X)", r"\1X", text, count=1)}]

    monkeypatch.setattr(
        build_feed, "_get_translation_pipeline", lambda: debris_pipeline
    )

    out = build_feed._translate_text_attempt(DE_LINES)
    assert out is not None
    assert "14AX" not in out
    assert "12A" in out and "14A" in out


def test_translation_cache_epoch_was_bumped() -> None:
    """Without the bump this fix changes nothing a subscriber can see.

    Both defects were cached as *successes* — no residual sentinel, so
    ``_cached_translation`` stored them as the canonical English. The
    Sticky-German guard cannot evict them either: it only retries when the
    cached value equals the German source, and "lines 12A and 14AX" is
    wrong without being German.

    The relocation notice that names the wrong address runs until
    18.09.2027.
    """
    assert build_feed._TRANSLATION_CACHE_EPOCH >= 8
