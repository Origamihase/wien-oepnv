"""The 180-character limit of ``_truncate_summary_180`` sits exactly at 180.

``_truncate_summary_180`` returns a summary of up to 180 characters unchanged.
A longer one is cut inside its first 175 characters at a word boundary and
gets " …" appended, so a cut result has at most 177 characters.

The tests hold both sides of the comparison ``len(summary) <= 180``:

* With ``<= 181`` (or ``< 182``) a summary of 181 characters would come back
  unchanged: 181 characters long and without the ellipsis.
  ``test_text_one_over_the_limit_is_cut`` fails.
* With ``<= 179`` (or ``< 180``) a summary of 180 characters would be cut.
  ``test_text_at_the_limit_stays_unchanged`` fails.

In the mutation run of the code audit the suite still passed with ``<= 181``.

The tests hold the place of the cut as well (review of 2026-11-10: with
``summary[:176]`` every test of the suite still passed):

* A text without a space has no word boundary; its first 175 characters stay
  and the result is exactly 177 characters long. With ``summary[:176]`` or
  ``summary[:179]`` it would be longer, with ``summary[:174]`` shorter.
  ``test_a_text_without_a_word_boundary_keeps_its_first_175_characters`` fails.
* ``test_text_one_over_the_limit_is_cut`` names the whole result for the
  181-character fixture.
"""

from __future__ import annotations

import pytest

from src.build_feed import _truncate_summary_180

_LIMIT = 180
# The cut is looked for inside the first 175 characters; with the ellipsis a
# cut result has at most 177.
_CUT = 175
_ELLIPSIS = " …"
_LONGEST_CUT_RESULT = _CUT + len(_ELLIPSIS)
_SENTENCE = "Haltestelle Karlsplatz verlegt "
# Five sentences (155 characters) and a tail that brings the text to the limit.
_AT_LIMIT = _SENTENCE * 5 + "Ersatz ab Kärntner Ring 1"
_ONE_OVER = _AT_LIMIT + "2"


def _text_of_length(length: int) -> str:
    """Running text of exactly ``length`` characters that does not end in a space."""
    text = (_SENTENCE * (length // len(_SENTENCE) + 1))[:length]
    if text.endswith(" "):
        text = text[:-1] + "."
    assert len(text) == length
    return text


def test_fixtures_have_the_lengths_the_tests_name() -> None:
    assert len(_AT_LIMIT) == _LIMIT
    assert len(_ONE_OVER) == _LIMIT + 1


def test_text_at_the_limit_stays_unchanged() -> None:
    assert _truncate_summary_180(_AT_LIMIT) == _AT_LIMIT


def test_text_one_over_the_limit_is_cut() -> None:
    out = _truncate_summary_180(_ONE_OVER)

    assert out != _ONE_OVER
    assert len(out) <= _LONGEST_CUT_RESULT
    assert out.endswith(_ELLIPSIS)
    # The 175th character stands inside "Ring": the cut falls back behind "Kärntner".
    assert _ONE_OVER[:_CUT].endswith("Kärntner R")
    assert out == _SENTENCE * 5 + "Ersatz ab Kärntner …"
    assert len(out) == 175


def test_cut_falls_on_a_word_boundary_of_the_input() -> None:
    out = _truncate_summary_180(_ONE_OVER)
    head = out.removesuffix(_ELLIPSIS)

    # What stands in front of the ellipsis is the beginning of the input,
    # and the input goes on with a space there: no word is cut in half.
    assert _ONE_OVER.startswith(head)
    assert _ONE_OVER[len(head)] == " "
    assert not head.endswith(" ")


@pytest.mark.parametrize("length", [1, 100, 175, 176, 177, 178, 179, 180])
def test_up_to_the_limit_nothing_changes(length: int) -> None:
    text = _text_of_length(length)
    assert _truncate_summary_180(text) == text


@pytest.mark.parametrize("length", [181, 182, 183, 190, 250, 400, 1000])
def test_beyond_the_limit_the_result_fits_and_ends_with_the_ellipsis(length: int) -> None:
    text = _text_of_length(length)
    out = _truncate_summary_180(text)

    assert len(out) <= _LONGEST_CUT_RESULT == 177
    assert out.endswith(_ELLIPSIS)
    assert text.startswith(out.removesuffix(_ELLIPSIS))


@pytest.mark.parametrize("length", [181, 182, 250, 1000])
def test_a_text_without_a_word_boundary_keeps_its_first_175_characters(length: int) -> None:
    text = "x" * length

    out = _truncate_summary_180(text)

    assert out == "x" * 175 + _ELLIPSIS
    assert len(out) == _LONGEST_CUT_RESULT


def test_a_word_boundary_at_the_end_of_the_first_175_characters_is_the_cut() -> None:
    """The 175th character is a space: everything in front of it stays."""
    text = "x" * 174 + " " + "y" * 20
    assert len(text) > _LIMIT and text[_CUT - 1] == " "

    assert _truncate_summary_180(text) == "x" * 174 + _ELLIPSIS
