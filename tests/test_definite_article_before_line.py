"""German articles its transit lines; English transit usage does not.

``die Linie 17A`` / ``die Linien 36A und 36B`` — the article is obligatory
in German and the model renders it faithfully. It is not being careless:
the identifier behind the article is a masked placeholder, so the model
has nothing to go on. English transit writing, including Wiener Linien's
own English pages, says "line 17A is diverted".

Measured over 294 unique published EN items, 23 texts carry it::

    the lines 36A and 36B are being redirected
    The line 79B is redirected in both directions
    Trains stop on the lines 1, 18, 62 WLB, the line O
    the folding ramps of the underground trains of the line U4

The lookahead for an identifier is what keeps the rule honest. ``the line
is divided`` and ``at the end of the line`` are ordinary English; only an
article standing directly in front of ``line``/``lines`` plus a line
identifier is dropped.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from src import build_feed
from src.build_feed import _drop_article_before_line


@pytest.mark.parametrize(
    ("published", "expected"),
    [
        (
            "Because of roadworks in the Kästenbaumgasse, the line 17A is being "
            "redirected.",
            "Because of roadworks in the Kästenbaumgasse, line 17A is being "
            "redirected.",
        ),
        (
            "the lines 36A and 36B are being redirected.",
            "lines 36A and 36B are being redirected.",
        ),
        (
            "the folding ramps of the underground trains of the line U4 cannot "
            "be extended.",
            "the folding ramps of the underground trains of line U4 cannot "
            "be extended.",
        ),
        (
            "Because of track construction works, the line D is divided.",
            "Because of track construction works, line D is divided.",
        ),
    ],
)
def test_each_published_case(published: str, expected: str) -> None:
    assert _drop_article_before_line(published) == expected


def test_a_sentence_initial_article_hands_its_capital_to_the_noun() -> None:
    """``The line 79B …`` must not become a lower-case sentence start."""
    assert (
        _drop_article_before_line("The line 79B is redirected in both directions.")
        == "Line 79B is redirected in both directions."
    )


def test_two_articles_in_one_sentence_both_go() -> None:
    assert (
        _drop_article_before_line("Trains stop on the lines 1, 18, 62 WLB, the line O")
        == "Trains stop on lines 1, 18, 62 WLB, line O"
    )


def test_only_the_article_before_the_line_goes() -> None:
    """``the call bus 86A`` keeps its article — it is not a line identifier."""
    assert (
        _drop_article_before_line(
            "the lines 86A, 87A and the call bus 86A are redirected."
        )
        == "lines 86A, 87A and the call bus 86A are redirected."
    )


@pytest.mark.parametrize(
    "ordinary_english",
    [
        "Because of track construction works, the line is divided.",
        "Passengers wait at the end of the line.",
        "The lines are closed.",
        "The line was reopened yesterday.",
        "Hold the line.",
    ],
)
def test_ordinary_english_is_left_alone(ordinary_english: str) -> None:
    """No identifier behind the noun, no substitution."""
    assert _drop_article_before_line(ordinary_english) == ordinary_english


def test_a_word_ending_in_the_is_not_an_article() -> None:
    """The lookbehind keeps ``…the`` inside a word from matching."""
    assert _drop_article_before_line("Blithe lines 5 run early.") == (
        "Blithe lines 5 run early."
    )


# ---------------- the rule must actually run ----------------


def test_the_rule_is_wired_into_the_translation_path(monkeypatch: Any) -> None:
    """Isolated unit tests cannot tell whether anything calls this.

    A mutation that simply removes the call from ``_translate_text_attempt``
    left every other test in this file passing. This is the one that fails,
    so the rule cannot quietly stop running. Same guard as
    ``test_indefinite_article_agreement.py``, for the same reason.
    """

    def model(text: str, **kwargs: Any) -> list[dict[str, str]]:
        # Marian's own behaviour: it carries the German article across and
        # keeps the masked identifier. Echoing the placeholder back matters —
        # a mock that dropped it would be rejected by the entity guard and
        # this test would fail for the wrong reason.
        placeholder = re.search(r"XENT\w+X\d+X", text)
        assert placeholder is not None, text
        return [
            {"translation_text": f"the line {placeholder.group(0)} is redirected"}
        ]

    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: model)
    out = build_feed._translate_text_attempt("Die Linie 17A wird umgeleitet")

    assert out is not None
    assert "the line" not in out, out
    assert "line" in out, out


# ---------------- the fix must reach the reader ----------------


def test_translation_cache_epoch_was_bumped() -> None:
    """All 23 occurrences were cached as successes.

    "the line 17A" is wrong without being German, so the Sticky-German
    guard will not retry it and the entity guard sees nothing missing.
    Without a bump the cached items keep their article for their lifetime —
    a U4 construction notice runs into 2026-11.
    """
    assert build_feed._TRANSLATION_CACHE_EPOCH >= 13
