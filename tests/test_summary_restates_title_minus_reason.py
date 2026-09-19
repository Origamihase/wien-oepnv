"""A ticker text that is the title minus its reason word — with WL's ``< >``.

Published 2026-09-19, item 7::

    74A: Demonstration Betrieb ab Landstraße
    Betrieb ab Landstraße <

Two things went wrong at once. WL's display boards mark a stop served in
both directions as ``< >`` — two arrows with a space between — and the
trailing-marker pattern knew only contiguous arrows, so the ``>`` came off
and the ``<`` reached the display. And the remaining text says nothing the
title does not: it is the title body without its reason word. Ten of 247
published pairs from 17.–19.09. restated the title that way
(``1A: Veranstaltung Kein Betrieb`` over ``Kein Betrieb``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest

from src import build_feed
from src.build_feed import (
    _strip_trailing_directional_marker,
    _summary_is_title_without_reason,
)
from src.feed.merge import _trim_trailing_directional
from src.feed_types import FeedItem


def _render(raw_title: str, raw_desc: str) -> str:
    item = cast(
        FeedItem,
        {
            "title": raw_title,
            "description": raw_desc,
            "source": "Wiener Linien",
            "category": "Störung",
            "guid": "t",
            "link": "",
        },
    )
    formatted = build_feed._format_item_content(
        item,
        ident="t",
        starts_at=datetime(2026, 9, 19, 11, 0, tzinfo=UTC),
        ends_at=datetime(2026, 9, 19, 21, 55, tzinfo=UTC),
    )
    return formatted.desc_text_truncated


# ---------------- the published item, end to end ----------------


def test_the_published_item_shows_no_arrow_and_no_repeat() -> None:
    assert _render("74A: Demonstration Betrieb ab Landstraße", "Demonstration\nBetrieb ab Landstraße < >") == "[Am 19.09.2026]"


def test_a_bare_repeat_without_the_reason_word_is_dropped() -> None:
    assert _render("1A: Veranstaltung Kein Betrieb", "Kein Betrieb") == "[Am 19.09.2026]"


def test_text_with_content_of_its_own_stays_but_loses_the_arrows() -> None:
    desc = _render("74A: Demonstration", "Betrieb ab Landstraße bis 20 Uhr < >")
    assert desc == "Betrieb ab Landstraße bis 20 Uhr [Am 19.09.2026]"


# ---------------- the arrow pattern, both copies ----------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Betrieb ab Landstraße < >", "Betrieb ab Landstraße"),
        ("Betrieb ab Landstraße <>", "Betrieb ab Landstraße"),
        ("Betrieb ab Landstraße >", "Betrieb ab Landstraße"),
        ("Betrieb ab Landstraße", "Betrieb ab Landstraße"),
        # An arrow inside the text is not a trailing marker.
        ("Betrieb < ab Landstraße", "Betrieb < ab Landstraße"),
    ],
)
def test_both_trailing_arrow_patterns_agree(text: str, expected: str) -> None:
    assert _strip_trailing_directional_marker(text) == expected
    assert _trim_trailing_directional(text) == expected


# ---------------- the check ----------------


@pytest.mark.parametrize(
    ("summary", "title", "category_word", "expected"),
    [
        ("Kein Betrieb", "1A: Veranstaltung Kein Betrieb", "", True),
        ("Betrieb ab Landstraße", "74A: Demonstration Betrieb ab Landstraße", "", True),
        ("kein betrieb", "1A: Veranstaltung Kein Betrieb", "", True),
        # The word was on both sides and stripped from the summary: the case
        # the branch always covered.
        ("Kein Betrieb", "1A: Veranstaltung Kein Betrieb", "Veranstaltung", True),
        # A different reason in the summary is information the title lacks.
        ("Kein Betrieb", "1A: Veranstaltung Kein Betrieb", "Gleisbauarbeiten", False),
        # Not the whole remainder: stays.
        ("Kein Betrieb. Grund: Unfall.", "1A: Veranstaltung Kein Betrieb", "", False),
        # The title has no reason word: not this rule's business.
        ("Kein Betrieb", "1A: Kein Betrieb", "", False),
        ("", "1A: Veranstaltung Kein Betrieb", "", False),
    ],
)
def test_summary_is_title_without_reason(summary: str, title: str, category_word: str, expected: bool) -> None:
    assert _summary_is_title_without_reason(summary, title, category_word) is expected


def test_a_different_reason_in_the_text_is_kept() -> None:
    """The title says Veranstaltung, the ticker says Gleisbauarbeiten: keep it.

    The prefix strip upstream only removes a reason word the title also
    carries, so this one stays inline — and the rule here must not empty
    a text that names a reason the title does not.
    """
    desc = _render("1A: Veranstaltung Kein Betrieb", "Gleisbauarbeiten\nKein Betrieb")
    assert desc.startswith("Gleisbauarbeiten Kein Betrieb"), desc
