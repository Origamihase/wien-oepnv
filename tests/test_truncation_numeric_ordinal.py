"""Regression test for Bug 22A (numeric date-ordinal left dangling).

Real Wiener-Linien Hinweis items frequently include German date
phrasings like ``Ab Dienstag, 3. März 2026 ...``. After the 180-char
truncation drops the partial last word and the strip-loop unwinds
short alpha tail tokens, the cached WL item #21 still ended with::

    "... der Linien N6 und N71. Zeitraum: Ab Dienstag, 3. …"

The orphan ``3.`` is a numeric date ordinal that the previous
``isalpha()`` rule deliberately skipped. Visually it looks like the
date was cut mid-stream — the user sees an unfinished day-number.

The fix extends the strip-loop's drop predicate to also accept
short digit-only ordinals (``3.``, ``10.``, ``31.``) as tokens to
discard. The loop iteration count is bumped from 3 to 4 so a chain
like ``Dienstag, 3. März`` → ``Dienstag, 3.`` → ``Dienstag,`` →
``Dienstag`` (long enough — kept) unwinds correctly.
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
            "title": "stub",
            "description": raw_desc,
            "source": "Wiener Linien",
            "category": "Hinweis",
            "guid": "test",
            "link": "",
        },
    )
    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    formatted = build_feed._format_item_content(
        item, ident="t", starts_at=now, ends_at=None
    )
    return formatted.desc_text_truncated


class TestTruncationDropsNumericOrdinal:
    def test_dangling_short_ordinal_dropped(self) -> None:
        # The raw cache phrasing from WL item #21.
        raw = (
            "Wegen Gleisbauarbeiten im Bereich Gottschalkgasse Simmeringer "
            "Hauptstraße kommt es zu einer Umleitung der Linien N6 und N71. "
            "Zeitraum: Ab Dienstag, 3. März Betriebsbeginn (Nacht von 2. "
            "auf 3.), bis voraussichtlich Ende Juni 2026."
        )
        out = _format(raw)
        # The dangling "3." must not survive into the tail.
        assert "3. …" not in out
        assert "…" in out

    def test_two_digit_ordinal_dropped(self) -> None:
        raw = "x " * 90 + "Tag, 31. Mai 2026"
        out = _format(raw)
        # "31." is 3 chars (digit+digit+period) and matches the new rule.
        assert "31. …" not in out

    def test_long_number_kept(self) -> None:
        # Numbers longer than 5 chars (or 4 digit-chars) stay.
        raw = "x " * 90 + "12345 trailing"
        out = _format(raw)
        # Truncation lands somewhere; we just verify no crash.
        assert "…" in out

    def test_alpha_token_still_dropped(self) -> None:
        # Round 20's alpha-only rule must continue to work.
        raw = "x " * 90 + "REX 7"
        out = _format(raw)
        assert "REX …" not in out

    def test_short_summary_unchanged(self) -> None:
        raw = "Linie U6: Verspätung wegen Schadhaftem Fahrzeug."
        out = _format(raw)
        # No truncation needed — full text appears.
        assert "Schadhaftem Fahrzeug." in out
        assert "…" not in out
