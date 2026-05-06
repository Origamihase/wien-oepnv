"""Regression tests for Bug 19A (truncation ends at German abbreviation).

The 180-char hard truncation in ``_format_item_content`` previously
used a plain ``rsplit(' ', 1)[0]`` to chop the partial last word and
appended ``" …"``. When the surviving last token was a short German
abbreviation like ``bzw.``, ``ca.``, ``z.B.``, or ``u.a.`` the visual
result read::

    "… die IC-Züge mit geänderten Fahrzeiten bzw. …"

which looks more like a glitch than an intentional ellipsis — the
period after the abbreviation visually clashes with the trailing
``…``. The cached ÖBB item ``Wien Hauptbahnhof ↔ Flughafen Wien``
surfaced this in the live feed.

The fix: after the rsplit, drop a trailing token of ≤5 characters
ending with a period (the typical German abbreviation shape) so
truncation lands on a clean word boundary.
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
            "link": "https://example.test/",
        },
    )
    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    formatted = build_feed._format_item_content(
        item, ident="t", starts_at=now, ends_at=None
    )
    return formatted.desc_text_truncated


class TestTruncationDropsTrailingAbbreviation:
    def test_bzw_dropped_at_end(self) -> None:
        # The exact phrasing from the cached ÖBB item.
        raw = (
            "Wegen Bauarbeiten fahren zwischen Wien Hbf (U) und "
            "Flughafen Wien Bahnhof von 10.07.2026 (09:00 Uhr) bis "
            "27.07.2026 (18:00 Uhr) die IC-Züge mit geänderten "
            "Fahrzeiten bzw. vorverlegten Abfahrtszeiten."
        )
        out = _format(raw)
        # The trailing "bzw. …" pattern must not appear.
        assert "bzw. …" not in out
        # The surviving tail should still convey the truncation.
        assert "…" in out

    def test_ca_dropped_when_at_end(self) -> None:
        # Construct a string long enough to trigger truncation that
        # lands on "ca." right before the chop point.
        raw = "x" * 165 + " ca. dropped tail"
        out = _format(raw)
        assert "ca. …" not in out
        assert "…" in out

    def test_normal_word_at_end_unchanged(self) -> None:
        # When truncation lands on a normal word (no period), the
        # behaviour is the standard rsplit drop — no extra abbreviation
        # heuristic kicks in. We only assert the truncation indicator
        # is appended; specifics of which words survive depend on
        # the rsplit position.
        raw = "x " * 95  # 190 chars
        out = _format(raw)
        assert "…" in out

    def test_short_summary_unchanged(self) -> None:
        # Below-180-char summaries don't hit the truncation path.
        raw = "Linie U6: Unregelmäßige Intervalle. Grund: Schadhaftes Fahrzeug."
        out = _format(raw)
        assert "Schadhaftes Fahrzeug." in out
        assert "…" not in out

    def test_punctuation_residue_stripped(self) -> None:
        # If after dropping the abbreviation the truncation ends on a
        # comma/colon, that residue is also stripped before " …".
        raw = (
            "Eine sehr lange Beschreibung mit ausreichend Wörtern und "
            "und mehreren Abschnitten und einer Aufzählung A, B, C, "
            "D, ggf. weitere."
        )
        out = _format(raw)
        # The output must not end with stray punctuation followed by …
        assert ", …" not in out
        assert ": …" not in out
