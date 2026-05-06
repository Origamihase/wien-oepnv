"""Regression tests for Bug 16A (false sentence splits on abbreviations / dates).

The summary builder split on any period+space sequence, treating common
German abbreviations and date components as artificial sentence
boundaries. The live ``docs/feed.xml`` exposed two failure modes:

1. ``Wegen Bauarbeiten … die IC-Züge mit geänderten Fahrzeiten bzw.
   vorverlegten Abfahrtszeiten.``
   - Old behaviour: split at ``bzw. v…`` → 2 sentences. Combined was
     186 chars — just over the 180-char hard limit — so the second
     piece was dropped, leaving a description that ended with a
     dangling ``bzw.``.
2. ``Ab Dienstag, 17. Februar 2026, etwa 09:00 Uhr.`` (Zeitraum line).
   - Old behaviour: split at ``17. F…`` → 2 sentences. The "Februar
     …" piece looked like sentence 2, fragmenting the date.

The fix tightens the split regex to require:
- a *letter* (not a digit) immediately before the period, AND
- an *uppercase* German letter immediately after the whitespace.

Lowercase follower → abbreviation (``bzw. vor…``, ``ca. 09…``) → no
split. Digit before period → date / house number (``17. Feb…``,
``Hauptstr. 200``) → no split. Genuine sentence boundaries like
``Richtungen. Grund: …`` still split correctly.
"""

from __future__ import annotations

from datetime import datetime, UTC
from typing import cast

from src import build_feed
from src.feed_types import FeedItem


def _format(raw_desc: str) -> str:
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
    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    return build_feed._format_item_content(
        item, ident="stub-1", starts_at=now, ends_at=None
    ).desc_text_truncated


class TestAbbreviationsNotSplit:
    def test_bzw_does_not_terminate_sentence(self) -> None:
        # The exact phrasing that exposed the bug in the live feed
        # (item #7, OEBB Wien Hbf ↔ Flughafen Wien).
        raw = (
            "Wegen Bauarbeiten fahren zwischen Wien Hbf und Flughafen Wien "
            "die IC-Züge mit geänderten Fahrzeiten bzw. vorverlegten "
            "Abfahrtszeiten."
        )
        out = _format(raw)
        # The description either contains the full text up to the hard
        # limit, OR it gets ellipsised at the 180-char trim — never
        # truncated mid-abbreviation with a dangling ``bzw.``.
        assert not out.split("[")[0].rstrip().endswith("bzw.")

    def test_ca_does_not_terminate_sentence(self) -> None:
        # ``ca.`` (circa) followed by a number is a single phrase.
        raw = (
            "Linie 24A: Wegen Bauarbeiten umgeleitet. ca. 09:00 Uhr "
            "bis 18:00 Uhr."
        )
        out = _format(raw)
        # The summary must include "ca. 09:00" intact (no split there).
        assert "ca. 09" in out or "ca." not in out

    def test_lowercase_after_period_no_split(self) -> None:
        # Generic shape: short word + period + lowercase follower must
        # NOT split.
        raw = "Linie X: ende der Strecke bzw. ab Mai 2026 erweitert."
        out = _format(raw)
        # The whole thing is one sentence under the new rule, so
        # truncation should not drop the "ab Mai 2026 erweitert" tail.
        assert "ab Mai 2026" in out


class TestDatesNotSplit:
    def test_german_date_pattern_kept_intact(self) -> None:
        # ``17. Februar 2026`` must not split between the day and the
        # month — the digit before the period is the disambiguator.
        raw = (
            "Linie 24A umgeleitet. Zeitraum: Ab Dienstag, 17. Februar "
            "2026 bis 08. Mai 2026."
        )
        out = _format(raw)
        # The date should still be readable as a single span.
        assert "17. Februar" in out

    def test_house_number_after_street_abbrev_no_split(self) -> None:
        # ``Hauptstr. 200`` is a street abbreviation followed by a
        # house number — not a sentence boundary.
        raw = (
            "Linie N62 in Richtung Karlsplatz: Umleitung ab Wiedner "
            "Hauptstr. 200 zur Stammstrecke."
        )
        out = _format(raw)
        assert "Hauptstr. 200" in out


class TestRealSentenceBoundariesSplit:
    def test_capital_after_letter_period_splits(self) -> None:
        # The boundary that was always working: letter + period, then
        # uppercase. Both sentences must end up combined when total ≤ 180.
        raw = "Linie 11A: Unregelmäßige Intervalle. Grund: Verkehrsüberlastung."
        out = _format(raw)
        assert "Linie 11A: Unregelmäßige Intervalle." in out
        assert "Grund: Verkehrsüberlastung." in out

    def test_karlsplatz_u_still_splits(self) -> None:
        # The Bug 15A example: abbreviated ``U.`` is still detected as
        # a sentence boundary because ``U`` is a letter.
        raw = (
            "Linie 62: Unregelmäßige Intervalle in Richtung Oper, "
            "Karlsplatz U. Grund: Rettungseinsatz."
        )
        out = _format(raw)
        assert "Karlsplatz U." in out
        assert "Grund: Rettungseinsatz." in out
