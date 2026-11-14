"""Cleaning the model's answer before the guard reads it (``clean_review`` in ``src/markdown_stats/review_client.py``).

Topic file of the tests described in ``tests/_year_review_support.py``.
"""

from __future__ import annotations

import pytest

from src.markdown_stats import year_review as yr
from src.markdown_stats.review_client import MAX_CLEAN_CHARS, clean_review
from tests._year_review_support import _check, _with


# ---- cleaning ---------------------------------------------------------------------------


def test_clean_review_gives_one_paragraph_without_control_characters() -> None:
    raw = "  Erster Satz.\n\nZweiter\tSatz.\r\n Dritter​ Satz\x07.‮ "
    assert clean_review(raw) == "Erster Satz. Zweiter Satz. Dritter Satz."


def test_clean_review_removes_what_looks_like_a_credential() -> None:
    key = "AIza" + "B" * 35
    cleaned = clean_review(f"Der Schlüssel {key} steht im Text.")
    assert key not in cleaned and cleaned == "Der Schlüssel [entfernt] steht im Text."
    # The replacement fails the guard: such a text is never published.
    assert "Formatierungszeichen im Text: [, ]" in _check(_with(cleaned))


@pytest.mark.parametrize("value", [None, 5, ["Text"], {"rueckblick": "Text"}, b"Text"])
def test_clean_review_of_anything_but_a_string_is_empty(value: object) -> None:
    assert clean_review(value) == ""


def test_clean_review_keeps_markup_for_the_guard_and_caps_the_length() -> None:
    assert clean_review("Ein <b>Satz</b> mit *Stern*.") == "Ein <b>Satz</b> mit *Stern*."
    assert len(clean_review("Wort " * 5000)) <= MAX_CLEAN_CHARS > yr.MAX_REVIEW_CHARS
