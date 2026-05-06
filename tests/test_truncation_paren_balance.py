"""Regression tests for Bug 21A (unbalanced opening paren before ellipsis).

Real ÖBB descriptions frequently include date/time clauses wrapped in
parens like ``(jeweils 08:45 Uhr - 14:45 Uhr)``. After the 180-char
truncation drops the partial last word and the strip-loop unwinds
short tail tokens, several cache items still ended with a *dangling*
opening paren — the closing ``)`` had been past the cut-off point
and stripping the inner content didn't notice the orphan opener:

    Cache item #6: "(23:30 …"
    Cache item #11: "(22:00 …"
    Cache item #1: "(jeweils 08:45 …"

The fix: after the strip-loop, if the truncated text contains more
``(`` than ``)``, drop everything from the last unbalanced ``(``
onward. The truncation now lands on a complete word/date before the
clause begins, so the ellipsis reads as intentional.
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
            "source": "ÖBB",
            "category": "Störung",
            "guid": "test",
            "link": "",
        },
    )
    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    formatted = build_feed._format_item_content(
        item, ident="t", starts_at=now, ends_at=None
    )
    return formatted.desc_text_truncated


class TestTruncationDropsUnbalancedParen:
    def test_jeweils_clause_dropped(self) -> None:
        # The phrasing from cache item #1.
        raw = (
            "Wegen Bauarbeiten zwischen Flughafen Wien Bahnhof und "
            "Wolfsthal Bahnhof am 19.02.2026 am 19.03.2026 am 16.04.2026 "
            "am 21.05.2026 und am 18.06.2026 (jeweils 08:45 Uhr - "
            "14:45 Uhr) keine REX 7 Züge fahren."
        )
        out = _format(raw)
        # The dangling opening paren must not survive into the output.
        assert "(jeweils" not in out
        # Content before the paren is preserved.
        assert "18.06.2026" in out

    def test_time_paren_dropped(self) -> None:
        # The phrasing from cache item #6.
        raw = (
            "Wegen Bauarbeiten zwischen Wien Hbf (U) und Wien Floridsdorf "
            "Bahnhof (U) von 07.04.2026 (23:30 Uhr) bis 08.04.2026 "
            "(03:52 Uhr), von 05.05.2026 (23:30 Uhr) bis 06.05.2026 "
            "(03:52 Uhr) keine Züge."
        )
        out = _format(raw)
        # The trailing dangling "(23:30 …" must not appear.
        # (Earlier "(U)" / "(23:30 Uhr)" balanced parens are fine.)
        if "…" in out:
            content = out[:out.rindex(" […]")] if " […]" in out else out
            # Count parens in the truncated content.
            opens = content.count("(")
            closes = content.count(")")
            assert opens <= closes, f"Unbalanced: {content!r}"

    def test_balanced_parens_kept(self) -> None:
        # When the truncation lands on balanced parens, nothing extra
        # is dropped.
        raw = (
            "Wegen Bauarbeiten von 03.10.2026 bis 05.10.2026 fahren "
            "zwischen Wien Hbf (U) und Gramatneusiedl Bahnhof einige "
            "Nahverkehrszüge nicht. Reisende werden gebeten Alternativen "
            "zu nutzen die im Bereich der Innenstadt verfügbar sind."
        )
        out = _format(raw)
        # The balanced "(U)" stays.
        assert "(U)" in out

    def test_no_truncation_for_short_summary(self) -> None:
        # Short descriptions don't hit the truncation path at all.
        raw = (
            "Linie U6: Unregelmäßige Intervalle. Grund: Schadhaftes "
            "Fahrzeug."
        )
        out = _format(raw)
        assert "…" not in out
        assert "Schadhaftes Fahrzeug" in out
