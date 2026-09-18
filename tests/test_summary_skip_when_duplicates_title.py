"""Regression tests for Bug 27A (summary just repeats the title body).

After the Round 24/25 dedup strips a leading category prefix, many WL
Störung items end up with a summary that exactly matches the title
body (after the line-prefix is removed)::

    T: "41E: Ersatzbus 41E halten bei Währinger Str 200"
    D: "Ersatzbus 41E halten bei Währinger Str 200 [Seit 06.05.2026]"

The user reads the same text twice — once as the title, once as the
description summary — which is pure noise.

The fix drops the summary entirely when its content is a verbatim
case-insensitive copy of the title body. The description then renders
as just the timeframe ``[Seit 06.05.2026]``.

Cache items affected (live WL Störung at the time of writing):
#27, #28, #30, #31, #32, #33, #35, #38.
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


class TestSummaryDroppedWhenDuplicatesTitleBody:
    def test_ersatzbus_duplicate_dropped(self) -> None:
        title = "41E: Ersatzbus 41E hält gegenüber"
        desc = "Ersatzbus 41E hält gegenüber"
        _, out = _format(title, desc)
        # Description should NOT contain a repeat of the title body.
        assert "Ersatzbus" not in out
        # The timeframe still appears.
        assert "[Seit" in out

    def test_kein_betrieb_duplicate_dropped(self) -> None:
        title = "46: Kein Betrieb"
        desc = "Kein Betrieb"
        _, out = _format(title, desc)
        assert "Kein Betrieb" not in out
        assert "[Seit" in out

    def test_busse_halten_duplicate_dropped(self) -> None:
        title = "62A: Busse halten Breitenfurter Straße 236-238"
        desc = "Busse halten Breitenfurter Straße 236-238"
        _, out = _format(title, desc)
        # Title body must not be duplicated.
        assert "Breitenfurter Straße" not in out

    def test_distinct_summary_kept(self) -> None:
        # When the summary is genuinely different, it survives.
        title = "U6: Verspätung wegen Schadhaftem Fahrzeug"
        desc = "Linie U6: Unregelmäßige Intervalle in beiden Richtungen."
        _, out = _format(title, desc)
        assert "Unregelmäßige Intervalle" in out

    def test_summary_contains_timeframe_extra(self) -> None:
        # Even when the summary is just the title body, the timeframe
        # is still appended so the description isn't empty.
        title = "46: Kein Betrieb"
        desc = "Kein Betrieb"
        _, out = _format(title, desc)
        assert out.strip().startswith("[")

    def test_partial_match_does_not_drop(self) -> None:
        # Summary contains MORE than the title body — must not drop.
        title = "46: Kein Betrieb"
        desc = "Kein Betrieb. Reisende werden gebeten Alternativen zu nutzen."
        _, out = _format(title, desc)
        assert "Reisende werden gebeten" in out

    def test_case_insensitive_match(self) -> None:
        # Casefold compare: ``Linie U6`` vs ``LINIE U6`` is the same.
        title = "U6: Linie U6 gestört"
        desc = "Linie U6 gestört"
        _, out = _format(title, desc)
        # Description body should not duplicate the title body.
        assert "Linie U6 gestört" not in out


class TestCategoryWordOnBothSides:
    """The same redundancy, one step over: the category word is in both.

    The leading WL category word (``Veranstaltung``, ``Demonstration``,
    ``Kranarbeiten`` …) is stripped from the SUMMARY, never from the
    title. When upstream put it in both, the comparison above weighs a
    stripped string against an unstripped one, misses, and the reader
    gets the same words twice::

        T: 2A: Veranstaltung Kein Betrieb
        D: Kein Betrieb [Am 18.09.2026]

    Counted over the published German feed: 12 of 222 unique items had
    exactly this shape, and in every one of them the only difference was
    that one word.
    """

    def test_veranstaltung_on_both_sides_drops_the_summary(self) -> None:
        # Live in docs/feed.xml on 2026-09-18, item 5 of ten.
        title = "40/41/9/42: Veranstaltung Linien 40 und 41 Umleitung über Linien 9 und 42"
        desc = "Veranstaltung\nLinien 40 und 41\nUmleitung über\nLinien 9 und 42"
        _, out = _format(title, desc)
        assert "Umleitung über" not in out
        assert out.strip().startswith("[")

    def test_the_shortest_case(self) -> None:
        title = "2A: Veranstaltung Kein Betrieb"
        desc = "Veranstaltung\nKein Betrieb"
        _, out = _format(title, desc)
        assert "Kein Betrieb" not in out
        # Not "Grund: Veranstaltung." either — the headline says it already.
        assert "Grund" not in out

    def test_demonstration_too_it_is_not_one_word(self) -> None:
        title = "71: Demonstration Umleitung bis St. Marx über Linie D und 18"
        desc = "Demonstration\nUmleitung bis St. Marx\nüber Linie D und 18"
        _, out = _format(title, desc)
        assert "St. Marx" not in out
        assert "Grund" not in out

    def test_the_reason_rescue_is_untouched(self) -> None:
        """The other branch must keep working — it solves a different case.

        Here the title does NOT carry the category word, so the word is
        the only thing the headline does not already say and
        ``_reason_only_summary`` keeps it.
        """
        title = "12A: Betrieb ab Johnstraße U"
        desc = "Gleisbauarbeiten\nBetrieb ab Johnstraße U"
        _, out = _format(title, desc)
        assert "Grund: Gleisbauarbeiten." in out

    def test_a_summary_with_more_to_say_survives(self) -> None:
        """Only an exact leftover is redundant; anything more is content."""
        title = "31: Veranstaltung Betrieb ab Ring"
        desc = "Veranstaltung\nBetrieb ab Ring, Reisende bitte Linie 1 nutzen"
        _, out = _format(title, desc)
        assert "Reisende bitte Linie 1 nutzen" in out

    def test_prose_after_the_category_word_is_never_touched(self) -> None:
        title = "31: Verspätungen"
        desc = (
            "Gleisbauarbeiten\nWegen Fortschreiten der Arbeiten kommt es zu "
            "Verspätungen."
        )
        _, out = _format(title, desc)
        assert "Wegen Fortschreiten der Arbeiten" in out

    def test_a_word_outside_the_category_list_is_not_stripped(self) -> None:
        """The branch keys on the known WL category words, not on any noun.

        ``Betriebsstörung`` is a WL word but deliberately not one of the
        ``_CATEGORY_PREFIX_WORDS``, so nothing is stripped on either side
        and the summary stands as upstream wrote it. The title is made
        longer than the summary on purpose: with an identical title the
        older exact-match branch would drop it anyway and the probe would
        prove nothing.
        """
        title = "13A: Betriebsstörung Kein Betrieb ab 10 Uhr"
        desc = "Betriebsstörung\nKein Betrieb"
        _, out = _format(title, desc)
        assert out.startswith("Betriebsstörung Kein Betrieb")
