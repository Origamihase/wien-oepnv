"""German articles its street and square names; English leaves them bare.

``in der Althanstraße``, ``zum Karlsplatz`` — obligatory in German, and
the model carries the article across because the name behind it is a
masked placeholder. Published::

    Because of roadworks in the Kästenbaumgasse, …
    Trains will be redirected to the Karlsplatz.

5 occurrences across 310 published EN items — the smaller half of the
family whose bigger half (``the line 17A``) PR #1842 took.

Two decisions worth knowing before changing anything here:

* **Only the article goes.** Whether ``in Althanstraße`` should read
  ``on Althanstraße`` is a separate question with a far less certain
  answer. The preposition stays exactly as the model chose it.
* **The street test is** :data:`~src.build_feed._STREET_SUFFIX_RE` —
  the pattern the masker already uses to shield these names — rather
  than a second suffix list that could drift away from it. That reuse is
  also what keeps the rule off ``the Ernst-Happel-Stadion`` and ``the
  Wiener Linien``, where the article is defensible English.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from src import build_feed
from src.build_feed import _drop_article_before_street


@pytest.mark.parametrize(
    ("published", "expected"),
    [
        (
            "Trains will be redirected to the Karlsplatz.",
            "Trains will be redirected to Karlsplatz.",
        ),
        (
            "Because of roadworks in the Kästenbaumgasse, line 17A is redirected.",
            "Because of roadworks in Kästenbaumgasse, line 17A is redirected.",
        ),
        (
            "Because of track construction works in the Althanstraße, line D is "
            "divided.",
            "Because of track construction works in Althanstraße, line D is "
            "divided.",
        ),
    ],
)
def test_each_published_case(published: str, expected: str) -> None:
    assert _drop_article_before_street(published) == expected


def test_an_attributed_street_name_counts_too() -> None:
    """``Vordere``/``Hintere``/``Kleine`` … are part of the name."""
    assert (
        _drop_article_before_street("Works in the Vordere Zollamtsstraße.")
        == "Works in Vordere Zollamtsstraße."
    )


@pytest.mark.parametrize(
    "defensible_english",
    [
        "Because of an event at the Ernst-Happel-Stadion.",
        "Information is available from the Wiener Linien.",
        "Passengers wait at the end of the line.",
        "The station is closed.",
    ],
)
def test_what_is_not_a_street_name_keeps_its_article(defensible_english: str) -> None:
    """The rule is only as wide as the project's own street pattern."""
    assert _drop_article_before_street(defensible_english) == defensible_english


def test_the_street_test_is_the_maskers_own_pattern() -> None:
    """Reuse, not a second list — pin it so a copy cannot creep back in.

    If someone replaces the shared pattern with a private suffix list, the
    two definitions can drift and a name the masker shields stops being
    recognised here (or the other way round).
    """
    assert build_feed._STREET_SUFFIX_RE.pattern in (
        build_feed._ARTICLE_BEFORE_STREET_RE.pattern
    )


def test_a_word_ending_in_the_is_not_an_article() -> None:
    assert (
        _drop_article_before_street("Blithe Karlsplatz stops are busy.")
        == "Blithe Karlsplatz stops are busy."
    )


# ---------------- the rule must actually run ----------------


def test_the_rule_is_wired_into_the_translation_path(monkeypatch: Any) -> None:
    """Isolated unit tests cannot tell whether anything calls this.

    Removing the call from ``_translate_text_attempt`` leaves every test
    above passing. This is the one that fails — the same guard as in
    ``test_definite_article_before_line.py`` and
    ``test_indefinite_article_agreement.py``, where the gap showed up
    first.
    """

    def model(text: str, **kwargs: Any) -> list[dict[str, str]]:
        # Echo the masked placeholder back: a mock that dropped it would be
        # rejected by the entity guard and fail this test for the wrong reason.
        placeholder = re.search(r"XENT\w+X\d+X", text)
        assert placeholder is not None, text
        return [{"translation_text": f"Works in the {placeholder.group(0)} today"}]

    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: model)
    out = build_feed._translate_text_attempt("Arbeiten in der Althanstraße heute")

    assert out is not None
    assert "the Althanstraße" not in out, out
    assert "Althanstraße" in out, out


# ---------------- the fix must reach the reader ----------------


def test_translation_cache_epoch_was_bumped() -> None:
    """All five occurrences are cached as successes.

    "the Karlsplatz" is wrong without being German, so neither the
    Sticky-German guard nor the entity guard would ever evict it.
    """
    assert build_feed._TRANSLATION_CACHE_EPOCH >= 14
