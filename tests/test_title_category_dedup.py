"""Regression tests for Bug 24A (title category word duplicated in description).

Real WL Hinweis items render with an HTML ``<h2>`` (e.g.
``Gleisbauarbeiten``) which becomes the title body AND the first
word of the description. The user then sees:

    T: "9/40/41/42: Gleisbauarbeiten"
    D: "Gleisbauarbeiten Wegen umfangreicher Gleisbauarbeiten im Bereich Aumannplatz …"

That leading word reads as a duplicated category prefix.

The fix strips the leading category word from the summary when:

- The title body's first word equals the summary's first word
  (case-insensitive).
- That word is one of the well-known construction-category nouns
  (``Bauarbeiten``, ``Gleisbauarbeiten``, ``Straßenbauarbeiten``,
  ``Rohrleitungsarbeiten``, ``Kranarbeiten``, ``Veranstaltung``).

The category-word allowlist keeps the rule conservative so it
doesn't collapse legitimate clauses like ``Ersatzverkehr zwischen X
und Y`` (where the title also says ``Ersatzverkehr``) — the word
``Ersatzverkehr`` carries semantic content, not just a category.
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
            "category": "Hinweis",
            "guid": "test",
            "link": "",
        },
    )
    now = datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc)
    formatted = build_feed._format_item_content(
        item, ident="t", starts_at=now, ends_at=None
    )
    return formatted.title_out, formatted.desc_text_truncated


class TestCategoryRedundancyStripped:
    def test_gleisbauarbeiten_stripped(self) -> None:
        title = "9/40/41/42: Gleisbauarbeiten"
        desc = (
            "Gleisbauarbeiten Wegen umfangreicher Gleisbauarbeiten im "
            "Bereich Aumannplatz kommt es zu Änderungen im Liniennetz."
        )
        _, out = _format(title, desc)
        assert out.startswith("Wegen umfangreicher")

    def test_strassenbauarbeiten_stripped(self) -> None:
        title = "24A: Straßenbauarbeiten"
        desc = (
            "Straßenbauarbeiten Wegen Bauarbeiten im Bereich der "
            "Breitenleer Straße werden die Linien 24A und N24 umgeleitet."
        )
        _, out = _format(title, desc)
        assert not out.startswith("Straßenbauarbeiten")
        assert out.startswith("Wegen Bauarbeiten")

    def test_rohrleitungsarbeiten_stripped(self) -> None:
        title = "49A/50A/50B/N49: Rohrleitungsarbeiten"
        desc = (
            "Rohrleitungsarbeiten Wegen Bauarbeiten im Bereich Linzer "
            "Straße werden die Linien 50A und N49 umgeleitet."
        )
        _, out = _format(title, desc)
        assert not out.startswith("Rohrleitungsarbeiten")

    def test_two_word_title_strips_first_word(self) -> None:
        # Cache item ``56A/58B/60A: Bauarbeiten Friedensstraße`` has a
        # two-word title body. We only require the FIRST word to match
        # the summary's first word.
        title = "56A/58B/60A: Bauarbeiten Friedensstraße"
        desc = (
            "Bauarbeiten Wegen Straßenbauarbeiten im Bereich "
            "Friedensstraße kommt es zu Umleitungen."
        )
        _, out = _format(title, desc)
        assert not out.startswith("Bauarbeiten ")
        assert out.startswith("Wegen Straßenbauarbeiten")


class TestNonCategoryWordsPreserved:
    def test_ersatzverkehr_not_stripped(self) -> None:
        # The leading word ``Ersatzverkehr`` is NOT in the category
        # allowlist — it's a complete clause head, not a category
        # prefix. Must be preserved.
        title = "S1: Ersatzverkehr"
        desc = "Ersatzverkehr zwischen Floridsdorf und Praterstern."
        _, out = _format(title, desc)
        assert out.startswith("Ersatzverkehr")

    def test_no_match_no_strip(self) -> None:
        # Title and description don't share a first word — no strip.
        title = "53A/54A/54B: Ablenkung ab 8. Mai"
        desc = (
            "Veranstaltung Wegen einer Veranstaltung am Wolfrathplatz "
            "werden die Busse umgeleitet."
        )
        _, out = _format(title, desc)
        # Both "Veranstaltung" mentions stay.
        assert out.startswith("Veranstaltung")

    def test_short_summary_unchanged(self) -> None:
        title = "U6: Verspätung"
        desc = "Linie U6: Unregelmäßige Intervalle wegen schadhaftem Fahrzeug."
        _, out = _format(title, desc)
        assert out.startswith("Linie U6")
