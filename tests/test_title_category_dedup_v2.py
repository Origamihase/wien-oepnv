"""Regression tests for Bug 25A (category prefix when title body doesn't start with category).

Round 24 covered the case where the title body's FIRST word equaled
the description's first word AND was a known category noun. But many
WL items have a different shape:

    T: "62A: Busse halten Breitenfurter Straße 236-238"
    D: "Bauarbeiten Busse halten Breitenfurter Straße 236-238"

Here the title body starts with ``Busse`` (not a category), but the
description still prepends ``Bauarbeiten`` in front of what's
otherwise the title body verbatim. The Round 24 rule did NOT fire
because the title body's first word (``Busse``) didn't match the
summary's first word (``Bauarbeiten``).

The fix adds a second pattern: when the description starts with a
category word AND the description's *second* word matches the title
body's first word, strip the leading category word.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import cast

from src import build_feed
from src.feed_types import FeedItem


def _format(raw_title: str, raw_desc: str) -> tuple[str, str]:
    item = cast(
        FeedItem,
        {
            "title": raw_title,
            "description": raw_desc,
            "source": "Wiener Linien",
            "category": "Störung",
            "guid": "test",
            "link": "",
        },
    )
    now = datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc)
    formatted = build_feed._format_item_content(
        item, ident="t", starts_at=now, ends_at=None
    )
    return formatted.title_out, formatted.desc_text_truncated


class TestCategoryPrefixSecondPattern:
    def test_bauarbeiten_busse_halten_stripped(self) -> None:
        # Cache item #38.
        title = "62A: Busse halten Breitenfurter Straße 236-238"
        desc = "Bauarbeiten Busse halten Breitenfurter Straße 236-238"
        _, out = _format(title, desc)
        assert not out.startswith("Bauarbeiten")
        assert out.startswith("Busse halten")

    def test_gleisbauarbeiten_ersatzbus_stripped(self) -> None:
        # Cache item #28.
        title = "41E: Ersatzbus 41E hält gegenüber"
        desc = "Gleisbauarbeiten Ersatzbus 41E hält gegenüber"
        _, out = _format(title, desc)
        assert not out.startswith("Gleisbauarbeiten")
        assert out.startswith("Ersatzbus 41E")

    def test_no_match_no_strip(self) -> None:
        # Description starts with a category word but the title body's
        # first word doesn't match the SECOND word of the description.
        # The category word stays in place.
        title = "53A/54A/54B: Ablenkung ab 8. Mai"
        desc = (
            "Veranstaltung Wegen einer Veranstaltung am Wolfrathplatz "
            "werden die Busse umgeleitet."
        )
        _, out = _format(title, desc)
        # Title body first word ("Ablenkung") doesn't match desc[1] ("Wegen")
        # so the leading "Veranstaltung" stays.
        assert out.startswith("Veranstaltung Wegen")

    def test_round24_pattern_still_works(self) -> None:
        # The original Round 24 rule (title body and desc share first
        # word AND it's a category) must still strip.
        title = "9/40/41/42: Gleisbauarbeiten"
        desc = (
            "Gleisbauarbeiten Wegen umfangreicher Gleisbauarbeiten im "
            "Bereich Aumannplatz."
        )
        _, out = _format(title, desc)
        assert out.startswith("Wegen umfangreicher")

    def test_non_category_prefix_kept(self) -> None:
        # Description starts with a non-category word — must stay
        # untouched.
        title = "U6: Verspätung"
        desc = (
            "Linie U6: Unregelmäßige Intervalle wegen schadhaftem Fahrzeug."
        )
        _, out = _format(title, desc)
        assert out.startswith("Linie U6")
