"""A WL relocation record is a table. It must not go through a translator.

Published 2026-09-18, and still wrong after two attempts at it::

    DE: Von: 1. Haidequerstraße 2     Nach: 1. Haidequerstraße 510
    EN: From: 1. Haidequerstraße 510  Duration: From 1. Haidequerstraße 510

Marian dropped the placeholder holding ``2`` and looped on the rest, so the
English promoted the destination into the origin's place: a reader is told
the stop is moving away from 510. The entity guard added in the previous
round caught that and fell back to German, which is where the item has sat
ever since — correct, and not English. Measured across five consecutive
builds, including one after the field labels went into the glossary.

Adding the labels to the glossary was not enough because it left ``der
Linie``, ``in Richtung`` and ``Ab`` behind, so the block still went to the
model, and the model still had a run of eleven placeholders and no sentence
to hold on to. Closing that gap by glossing the remainder is not available
either: the corpus contains ``bei der Linie``, where ``of line`` would be
wrong.

So the record stops being translated at all. Every label resolves through
the glossary and every value is a stop name, a street or a house number that
the entity masker carries verbatim — mask, resolve, done. What reaches the
model is only the prose in front of it, which for the live item is
``□ der Linie □ in Richtung □``.

**The threshold is what keeps this narrow.** Over 157 distinct published
descriptions: 130 carry no label, 24 carry exactly one — nearly all the
``Grund: …`` tail, which translates correctly today and must stay prose —
and 3 carry two or more. Those 3 are exactly the relocation items. A split
at one label would have pulled 24 working items onto a new path for nothing.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from src import build_feed
from src.build_feed import (
    _FIELD_LABEL_EN,
    _GLOSSARY_BASE,
    _LABEL_RECORD_RE,
    _MIN_LABELS_FOR_RECORD,
    _join_record,
    _render_label_record,
    _split_label_record,
)

# The three live descriptions carrying a record, verbatim.
DE_72A = (
    "Haltestellenverlegung der Linie 72A in Richtung Hasenleitengasse "
    "Haltestelle: Kraftwerk Simmering Von: 1. Haidequerstraße 2 "
    "Nach: 1. Haidequerstraße 510 Dauer: Ab …"
)
DE_49A = (
    "Haltestellenverlegung der Linien 49A und 50B in Richtung Wolfersberg "
    "bzw. Auhof Haltestelle: Hüttergasse Von: Anzengruberstraße gegenüber 77a "
    "Nach: Hüttergasse 6A-6B Dauer: Ab …"
)


def _render(record: str) -> str:
    return _render_label_record(record, source=None, category=None)


# ---------------- the split ----------------


def test_the_record_is_split_off_the_prose() -> None:
    prose, record = _split_label_record(DE_72A)

    assert prose == "Haltestellenverlegung der Linie 72A in Richtung Hasenleitengasse"
    assert record.startswith("Haltestelle: Kraftwerk Simmering")
    assert record.endswith("Dauer: Ab …")


@pytest.mark.parametrize(
    "text",
    [
        "Unregelmäßige Intervalle in beiden Richtungen. Grund: Verunreinigung.",
        "Betrieb ab Schwedenplatz. Grund: Gleisbauarbeiten.",
    ],
)
def test_a_single_label_stays_prose(text: str) -> None:
    """24 of 157 published descriptions end in one label and read correctly.

    ``Grund: Verunreinigung.`` already translates as prose. Pulling it onto
    the record path would change 24 working items to gain nothing, and would
    strip the sentence in front of it of its closing context.
    """
    assert _split_label_record(text) == (text, "")


def test_prose_without_any_label_is_untouched() -> None:
    text = "Wegen einer Weichenstörung sind Zugfahrten nur eingeschränkt möglich."
    assert _split_label_record(text) == (text, "")


def test_the_threshold_is_two() -> None:
    """Pinned as a decision, not an accident — see the module docstring."""
    assert _MIN_LABELS_FOR_RECORD == 2


# ---------------- the rendering ----------------


def test_the_addresses_keep_their_roles() -> None:
    """The defect this exists for: origin and destination must not swap."""
    _, record = _split_label_record(DE_72A)
    out = _render(record)

    assert "From: 1. Haidequerstraße 2" in out
    assert "To: 1. Haidequerstraße 510" in out
    # The published wrong form, in full.
    assert "From: 1. Haidequerstraße 510" not in out


def test_the_whole_record_renders() -> None:
    _, record = _split_label_record(DE_72A)
    assert _render(record) == (
        "Stop: Kraftwerk Simmering From: 1. Haidequerstraße 2 "
        "To: 1. Haidequerstraße 510 Duration: From …"
    )


def test_the_second_live_record_renders() -> None:
    """``gegenüber`` had only its abbreviated ``ggü.`` form in the glossary.

    The model used to render the spelled-out word as "opposite". Once the
    record stops reaching the model, an unglossed word would simply stay
    German, so the long form had to be added.
    """
    _, record = _split_label_record(DE_49A)
    out = _render(record)

    assert "Stop: Hüttergasse" in out
    assert "From: Anzengruberstraße opposite 77a" in out
    assert "To: Hüttergasse 6A-6B" in out
    assert "gegenüber" not in out


def test_rendering_is_deterministic() -> None:
    """No model, so the same input must give the same output every time."""
    _, record = _split_label_record(DE_72A)
    assert _render(record) == _render(record)


# ---------------- what the model is handed ----------------


def test_the_model_never_sees_the_record(monkeypatch: Any) -> None:
    """The load-bearing assertion of this whole change.

    Marian degenerates on a run of placeholders with no sentence around it.
    Keeping the record away from it is the fix; everything else is detail.
    """
    seen: list[str] = []

    def echo(text: str, **kwargs: Any) -> list[dict[str, str]]:
        seen.append(text)
        return [{"translation_text": text}]

    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: echo)
    build_feed._translate_text_attempt(DE_72A)

    assert seen, "the prose should still have reached the model"
    for label in ("Haltestelle:", "Von:", "Nach:", "Dauer:"):
        assert not any(label in text for text in seen), (
            f"{label!r} reached the model: {seen!r}"
        )


def test_the_prose_still_reaches_the_model(monkeypatch: Any) -> None:
    """The split must not silently stop translating the sentence as well."""
    seen: list[str] = []

    def echo(text: str, **kwargs: Any) -> list[dict[str, str]]:
        seen.append(text)
        return [{"translation_text": text}]

    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: echo)
    build_feed._translate_text_attempt(DE_72A)

    assert len(seen) == 1
    assert "der Linie" in seen[0] and "in Richtung" in seen[0]


def test_prose_and_record_are_rejoined(monkeypatch: Any) -> None:
    def echo(text: str, **kwargs: Any) -> list[dict[str, str]]:
        return [{"translation_text": text}]

    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: echo)
    out = build_feed._translate_text_attempt(DE_72A)

    assert out is not None
    assert out.startswith("stop relocation")
    assert out.endswith("Duration: From …")
    assert "Stop: Kraftwerk Simmering" in out


def test_a_record_only_text_needs_no_model(monkeypatch: Any) -> None:
    """Nothing in front of the labels — there is no prose to translate."""
    seen: list[str] = []

    def echo(text: str, **kwargs: Any) -> list[dict[str, str]]:
        seen.append(text)
        return [{"translation_text": text}]

    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: echo)
    out = build_feed._translate_text_attempt(
        "Von: Anzengruberstraße 77a Nach: Hüttergasse 6A-6B"
    )

    assert out == "From: Anzengruberstraße 77a To: Hüttergasse 6A-6B"
    assert seen == [], f"the model was called for a pure record: {seen!r}"


def test_a_failing_prose_translation_still_rejects_everything(
    monkeypatch: Any,
) -> None:
    """A good record must not launder a broken sentence into the feed.

    If the model mangles the prose the item is still untrustworthy as a
    whole, so the existing fallback wins. Pinned because "the record is safe
    now" is exactly the reasoning that would tempt someone to ship the half
    that worked.
    """
    def dropping(text: str, **kwargs: Any) -> list[dict[str, str]]:
        return [{"translation_text": re.sub(r"XENT\w+?X\d+X", "", text, count=1)}]

    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: dropping)
    assert build_feed._translate_text_attempt(DE_72A) is None


# ---------------- one source of truth ----------------


def test_every_label_is_both_glossed_and_matched() -> None:
    """The splitter and the glossary derive from the same table.

    A label in one but not the other either translates inside a block the
    model still mangles, or is torn out of a block whose label then stays
    German. This is the test that fails if someone adds a label to only one
    of the two.
    """
    for german, english in _FIELD_LABEL_EN.items():
        assert _GLOSSARY_BASE.get(f"{german}:") == f"{english}:", german
        assert _LABEL_RECORD_RE.search(f"{german}: x"), german


def test_the_plural_label_is_not_matched_as_the_singular() -> None:
    """The plural must claim its own span — but NOT thanks to the sort order.

    A mutation reversing the longest-first sort leaves every assertion here
    passing, because the required ``:`` makes the engine backtrack out of
    ``Haltestelle`` on its own. The sort is symmetry with the glossary
    pattern, where it genuinely decides the match; here it decides nothing.
    Pinned with that stated so the behaviour is guarded without crediting
    the wrong mechanism for it.
    """
    match = _LABEL_RECORD_RE.search("Haltestellen: Hütteldorf, Ottakring")
    assert match is not None
    assert match.group(0).startswith("Haltestellen:")


def test_a_label_glued_to_a_preceding_word_is_not_a_label() -> None:
    """The ``(?<!\\w)`` guard, exercised by a case that actually needs it.

    ``Vonwegen: nein`` looks like the obvious probe and tests nothing — no
    ``:`` follows ``Von``, so it fails to match with or without the guard.
    The cases below are synthetic on purpose: real German writes compounds
    solid and lowercase (``Abfahrtszeitraum:``), so no published item can
    reach this branch today. The guard is defence against a future label
    landing inside a token, and it is worth the two characters — but it is
    pinned here as defence, not as something the live corpus exercises.
    """
    assert not _LABEL_RECORD_RE.search("XVon: y")
    assert not _LABEL_RECORD_RE.search("S1Von: y")
    # …while the ordinary form still matches.
    assert _LABEL_RECORD_RE.search("Umleitung Von: y")


# ---------------- joining ----------------


@pytest.mark.parametrize(
    ("translated", "record", "expected"),
    [
        ("prose", "record", "prose record"),
        ("prose", "", "prose"),
        ("", "record", "record"),
        ("", "", ""),
    ],
)
def test_join_record(translated: str, record: str, expected: str) -> None:
    assert _join_record(translated, record) == expected


# ---------------- the fix must reach the reader ----------------


def test_translation_cache_epoch_was_bumped() -> None:
    assert build_feed._TRANSLATION_CACHE_EPOCH >= 10
