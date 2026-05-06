"""Regression tests for Bug 20A (lone line-marker before truncation ellipsis).

After Round 19's abbreviation cleanup, real ÖBB cache items still
exposed two awkward truncation tails:

* ``IC 1110, IC 1113, IC …`` — the rsplit dropped the partial number
  but left the line-marker letters ``IC`` standing alone, which read
  as a glitch.
* ``Uhr - 1`` → ``Uhr -`` → after the dash strip → ``Uhr`` followed
  by ``…``: a lone unit token. Strictly speaking ``Uhr`` is the
  German word for "o'clock", so an ellipsis next to it implies the
  text continues mid-time-stamp.

The fix iterates the strip step: drop trailing punctuation, then drop
short letter-only tokens (≤5 chars, no digits), and repeat. This
unwinds compound tails like ``Uhr - 1`` → ``Uhr -`` → ``Uhr`` → drop.
The same rule handles ``IC`` and ``REX`` line markers without numbers.
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


class TestTruncationLineMarker:
    def test_lone_ic_dropped(self) -> None:
        # Construct a string where the truncation lands on a partial
        # IC train number, leaving "IC" alone.
        raw = (
            "Wegen Bauarbeiten zwischen Wien Westbahnhof und Wien Hütteldorf "
            "fahren von 03.06.2026 (23:00 Uhr) bis 08.06.2026 (04:00 Uhr) "
            "die Züge IC 1110, IC 1113, IC 1115, IC 1117 nicht."
        )
        out = _format(raw)
        assert "IC …" not in out, out
        assert "IC 1113" in out

    def test_lone_uhr_dropped_after_dash(self) -> None:
        # Construct a tail where rsplit leaves "Uhr -" then dash strip
        # exposes "Uhr" alone.
        raw = (
            "Wegen Bauarbeiten zwischen Wien und Wolfsthal am 19.02.2026 "
            "am 19.03.2026 am 16.04.2026 am 21.05.2026 und am 18.06.2026 "
            "(jeweils 08:45 Uhr - 14:45 Uhr) keine Züge."
        )
        out = _format(raw)
        assert "Uhr …" not in out, out

    def test_lone_rex_dropped(self) -> None:
        # Total length must exceed 180 to trigger truncation.
        raw = "x " * 90 + "REX 7"
        out = _format(raw)
        assert "REX …" not in out
        assert " …" in out

    def test_compound_uhr_dash_chain_unwound(self) -> None:
        # Compound: tail is "Uhr - 1" → "Uhr -" → "Uhr" → drop.
        raw = "x " * 90 + "Uhr - 12345"
        out = _format(raw)
        assert "Uhr …" not in out
        # The "Uhr" residue must not appear right before the ellipsis.
        assert "Uhr" not in out[-15:]

    def test_normal_long_word_kept_when_truncated(self) -> None:
        # Surviving last token is long enough — stays untouched.
        raw = ("x " * 90) + "Verkehrsunfall hat sich ereignet"
        out = _format(raw)
        # Truncation hits, ellipsis appears.
        assert " …" in out
