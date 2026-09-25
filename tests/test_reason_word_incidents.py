"""Incident causes get the same en dash as planned work in WL titles.

Published 2026-09-25 12:05, ``docs/feed.xml`` item 1::

    6: Fremdunfall Züge halten bei der Linie O

The C.5 separator (``tests/test_reason_word_separator.py``) puts an en dash
between a WL ticker's reason word and its consequence, but only knew the
planned-work words (Bauarbeiten, Veranstaltung, Demonstration …). Measured
over the WL cache since June 2026 (455 distinct titles), 30 titles open
with an incident cause instead: Fremdunfall, Rettungseinsatz,
Oberleitungsgebr(echen), Polizeieinsatz, Verkehrsunfall, Gleisschaden,
Feuerwehreinsatz, Polizeiübung. "Oberleitungsgebr" is the ticker's cut of
"Oberleitungsgebrechen" and is spelled out.

The English title splitter must know the same words: a dashed title it does
not recognise goes to the model whole, dash included — the shape that
produced "Demonstration –Xservice" on 2026-09-19.

Mutations checked against this file (each one caught, by the test named):

* the incident words are left out of ``_TITLE_REASON_WORDS`` →
  ``test_the_live_titles_get_the_dash``.
* ``_split_reason_title`` keeps checking ``_CATEGORY_PREFIX_WORDS`` only →
  ``test_the_english_splitter_knows_the_same_words``.
* the abbreviation is not spelled out →
  ``test_the_ticker_abbreviation_is_spelled_out``.
* the incident words are added to ``_CATEGORY_PREFIX_WORDS`` instead
  (summary dedupe and topic budget would change) →
  ``test_the_summary_word_list_is_unchanged``.
"""

from __future__ import annotations

import pytest

from src import build_feed
from src.build_feed import _separate_reason_word, _split_reason_title


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Verbatim from the WL cache.
        ("6: Fremdunfall Züge halten bei der Linie O", "6: Fremdunfall – Züge halten bei der Linie O"),
        ("35A: Fremdunfall Betrieb ab Glanzing", "35A: Fremdunfall – Betrieb ab Glanzing"),
        ("18: Rettungseinsatz Umleitung über Linien 6 und O", "18: Rettungseinsatz – Umleitung über Linien 6 und O"),
        ("13A: Polizeieinsatz Betrieb ab Pilgramgasse U", "13A: Polizeieinsatz – Betrieb ab Pilgramgasse U"),
        ("11: Verkehrsunfall Betrieb ab Absberggasse", "11: Verkehrsunfall – Betrieb ab Absberggasse"),
        ("49: Gleisschaden Betrieb ab Hütteldorfer Straße", "49: Gleisschaden – Betrieb ab Hütteldorfer Straße"),
        ("66A: Feuerwehreinsatz Betrieb über Breitenfurter Straße", "66A: Feuerwehreinsatz – Betrieb über Breitenfurter Straße"),
        ("1A: Polizeiübung Kein Betrieb", "1A: Polizeiübung – Kein Betrieb"),
    ],
)
def test_the_live_titles_get_the_dash(raw: str, expected: str) -> None:
    assert _separate_reason_word(raw) == expected


def test_the_ticker_abbreviation_is_spelled_out() -> None:
    assert _separate_reason_word("5: Oberleitungsgebr Betrieb ab Lerchenfelder Straße") == (
        "5: Oberleitungsgebrechen – Betrieb ab Lerchenfelder Straße"
    )
    assert _separate_reason_word("5: Oberleitungsgebr") == "5: Oberleitungsgebrechen"


@pytest.mark.parametrize(
    "title",
    [
        "44: Fremdunfall",  # nothing to separate
        "44: Fremdunfall am 25.09.2026",  # a sentence, not a fragment
        "Wien  Meidling ↔ Wien Liesing",  # untouched byte for byte
        "U2: Klapprampensperre am 27.09.2026",
        "6: Fremdunfall – Züge halten",  # idempotent
    ],
)
def test_other_titles_are_untouched(title: str) -> None:
    assert _separate_reason_word(title) == title


@pytest.mark.parametrize(
    "title",
    [
        "6: Fremdunfall – Züge halten bei der Linie O",
        "5: Oberleitungsgebrechen – Betrieb ab Lerchenfelder Straße",
        "1A: Polizeiübung – Kein Betrieb",
    ],
)
def test_the_english_splitter_knows_the_same_words(title: str) -> None:
    halves = _split_reason_title(title)
    assert halves is not None, title
    head, tail = halves
    assert " – " not in head and " – " not in tail


def test_the_summary_word_list_is_unchanged() -> None:
    # Summary dedupe and the topic budget read _CATEGORY_PREFIX_WORDS; the
    # incident words change the title only.
    assert "fremdunfall" not in build_feed._CATEGORY_PREFIX_WORDS
    assert build_feed._CATEGORY_PREFIX_WORDS < build_feed._TITLE_REASON_WORDS
