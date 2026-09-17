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

from datetime import datetime, UTC
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
    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    formatted = build_feed._format_item_content(
        item, ident="t", starts_at=now, ends_at=None
    )
    return formatted.title_out, formatted.desc_text_truncated


class TestCategoryPrefixSecondPattern:
    # Both cases below end in the same place: Round 25 strips the leading
    # category word, the remainder equals the title body, and Round 27 drops
    # it so the user does not read the same sentence twice.
    #
    # What Round 27 left behind was an empty body. For the WL *Hinweise* this
    # rule was built for that is right — their text continues after the
    # heading word. For the display-ticker *Störungen* it is not: there the
    # category word IS the reason and the only thing the title does not
    # already say. 11 of the 75 items in the 2026-09-17 cache reached the
    # feed as a headline over a bare ``[Seit 06.05.2026]``, never saying why.
    #
    # The assertions therefore pin what they always meant — the awkward
    # ``Bauarbeiten Busse halten …`` prefix must be gone and the title body
    # must not be repeated — while the reason is kept in WL's own wording.
    # ``assert "Bauarbeiten" not in out`` was only ever a proxy for that.
    def test_bauarbeiten_busse_halten_stripped(self) -> None:
        # Cache item #38.
        title = "62A: Busse halten Breitenfurter Straße 236-238"
        desc = "Bauarbeiten Busse halten Breitenfurter Straße 236-238"
        _, out = _format(title, desc)
        assert not out.startswith("Bauarbeiten Busse halten")
        assert "Busse halten Breitenfurter" not in out, "title body restated"
        assert out.startswith("Grund: Bauarbeiten."), out

    def test_gleisbauarbeiten_ersatzbus_stripped(self) -> None:
        # Cache item #28.
        title = "41E: Ersatzbus 41E hält gegenüber"
        desc = "Gleisbauarbeiten Ersatzbus 41E hält gegenüber"
        _, out = _format(title, desc)
        assert not out.startswith("Gleisbauarbeiten Ersatzbus")
        assert "Ersatzbus 41E hält gegenüber" not in out, "title body restated"
        assert out.startswith("Grund: Gleisbauarbeiten."), out

    def test_category_wegen_pattern_stripped_independent_of_title(self) -> None:
        # Description starts with a category word AND the next word is
        # ``Wegen`` — Round 37 added a third branch in
        # ``_strip_summary_category_prefix`` that recognises this as
        # the WL HTML heading-leak (real German prose never opens
        # with bare ``Veranstaltung Wegen …``) and strips the
        # category, independent of the title shape. Pre-Round-37 the
        # category word survived because the title-body comparison
        # branches required a shared first word.
        title = "53A/54A/54B: Ablenkung ab 8. Mai"
        desc = (
            "Veranstaltung Wegen einer Veranstaltung am Wolfrathplatz "
            "werden die Busse umgeleitet."
        )
        _, out = _format(title, desc)
        # Round 37: leading heading-word stripped.
        assert out.startswith("Wegen einer Veranstaltung")
        assert not out.startswith("Veranstaltung Wegen")

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
