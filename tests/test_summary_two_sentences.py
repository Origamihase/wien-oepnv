"""Regression test for Bug 15A (second sentence dropped before 180-char limit).

The summary builder in ``_format_item_content`` truncates feed
descriptions to a max-180-character ``[summary] [time-line]`` layout.
Previously the rule was: if the *first* sentence was already longer
than 60 characters, the second sentence was silently discarded —
even when concatenating both stayed well under the 180-char hard
limit.

The cached WL Störung item ``Linie 62: Unregelmäßige Intervalle in
Richtung Oper, Karlsplatz U. Grund: Rettungseinsatz.`` exposed the
defect in production. The abbreviation period after ``Karlsplatz U``
artificially terminates "sentence 1" at ~76 characters, so the cause
clause (``Grund: Rettungseinsatz.``, ~23 characters) was dropped and
the rendered feed only showed::

    Linie 62: … Karlsplatz U. [Am 06.05.2026]

even though both sentences combined are ~99 characters and fit
comfortably below 180.

The fix: append the second sentence whenever the combined length
stays within the existing 180-character hard limit.

Because ``_format_item_content`` lives behind a ``FormattedContent``
return type, the test interacts via ``_make_rss`` indirectly — but
the simplest verification is a direct call into the summary helper
once the trim logic is encapsulated. Here we exercise the public
behaviour: build a fake item, render it, and assert the rendered
description carries both sentences.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import cast

from src import build_feed
from src.feed_types import FeedItem


class TestSummaryKeepsSecondSentence:
    def _format(self, raw_desc: str) -> str:
        # Hit the same code path as the live pipeline by calling the
        # internal formatter directly. We don't need first_seen state
        # because the summary computation lives upstream of timing.
        item = cast(
            FeedItem,
            {
                "title": "Stub Title",
                "description": raw_desc,
                "source": "Wiener Linien",
                "category": "Störung",
                "guid": "stub-1",
                "link": "https://example.test/",
            },
        )
        now = datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc)
        formatted = build_feed._format_item_content(
            item, ident="stub-1", starts_at=now, ends_at=None
        )
        return formatted.desc_text_truncated

    def test_karlsplatz_u_keeps_both_sentences(self) -> None:
        # The exact phrasing from cache item #42 — the abbreviation
        # period after ``Karlsplatz U`` used to terminate sentence 1
        # at the 60-char threshold.
        raw = (
            "Linie 62: Unregelmäßige Intervalle in Richtung Oper, "
            "Karlsplatz U. Grund: Rettungseinsatz."
        )
        out = self._format(raw)
        assert "Karlsplatz U." in out
        assert "Grund: Rettungseinsatz." in out

    def test_short_first_sentence_includes_second(self) -> None:
        # Pre-existing behaviour — short first + short second combine.
        raw = (
            "Linie 11A: Unregelmäßige Intervalle in beiden Richtungen. "
            "Grund: Verkehrsüberlastung."
        )
        out = self._format(raw)
        assert "beiden Richtungen." in out
        assert "Grund: Verkehrsüberlastung." in out

    def test_combined_above_180_drops_second(self) -> None:
        # When concatenation would exceed the 180-char hard limit,
        # the second sentence stays out and only the first is kept.
        first = "x" * 170 + "."
        second = "Drop me because I do not fit."
        out = self._format(f"{first} {second}")
        assert first in out
        # The 175 trim + " …" suffix kicks in when the first alone
        # exceeds 180; either way the second sentence must NOT appear.
        assert "Drop me because I do not fit." not in out

    def test_single_sentence_unchanged(self) -> None:
        raw = "Linie 31: Verspätungen wegen Schadhaftem Fahrzeug."
        out = self._format(raw)
        assert "Linie 31: Verspätungen wegen Schadhaftem Fahrzeug." in out
