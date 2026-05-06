"""Regression tests for Bug 23A (chained line-marker list partially dropped).

Real ÖBB descriptions enumerate train numbers as ``IC 1110, IC 1113,
IC 1115, …``. After Round 19/20/22 fixes the strip-loop unwound the
partial last item but only ran 4 iterations. For long lists the loop
exited mid-pattern and left a dangling line marker before the
ellipsis::

    "... die Züge IC …"   ← cached ÖBB item #13

Worse, the existing ``isalpha()`` rule was a hammer: it dropped any
short alpha-only token, including legitimate German content words
(``Züge``, ``fahren``) if the iteration count let them surface as
the last token.

The fix:

- Bumps the iteration cap to 8 to handle long ``IC 1110, IC 1113,
  IC 1115, IC 1118, IC 1119, IC 1142, IC 1143`` lists.
- Splits the drop predicate into explicit cases so real content
  words are not dropped:

  • period-ending alpha → German abbreviation (``bzw.``, ``ca.``)
  • period-ending digit → date ordinal (``3.``, ``10.``)
  • plain digit → number list item (``1110``)
  • all-uppercase alpha → line marker (``IC``, ``REX``, ``RJX``)
  • known unit token → unit residue (``Uhr``, ``min``, ``km``)
  • everything else → real word, terminate loop
"""

from __future__ import annotations

from datetime import datetime, timezone
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
    now = datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc)
    formatted = build_feed._format_item_content(
        item, ident="t", starts_at=now, ends_at=None
    )
    return formatted.desc_text_truncated


class TestTruncationChainedMarker:
    def test_long_ic_list_unwinds_to_zuege(self) -> None:
        # Reproduction of cached ÖBB item #13.
        raw = (
            "Wegen Bauarbeiten können zwischen Wien Westbahnhof (U) und "
            "Wien Hütteldorf Bahnhof (U) von 03.06.2026 (23:00 Uhr) bis "
            "08.06.2026 (04:00 Uhr) die Züge IC 1110, IC 1113, IC 1115, "
            "IC 1118, IC 1119, IC 1142, IC 1143 und D 1141 nicht fahren."
        )
        out = _format(raw)
        # The dangling "IC …" tail must be gone.
        assert "IC …" not in out
        # The truncation lands on "Züge" (a real word) before the ellipsis.
        assert "Züge …" in out

    def test_real_content_word_not_dropped(self) -> None:
        # Even with a long-iteration loop, real German content words
        # must NOT be dropped — only markers, units, abbreviations and
        # ordinals are.
        raw = (
            "Wegen umfangreicher Bauarbeiten können zwischen Wien "
            "Westbahnhof und Wien Hütteldorf keine Züge fahren wegen "
            "Hindernissen umfangreichen Maßnahmen Verbindungen Reisende "
            "Fahrgäste haben Möglichkeit alternativen Verkehrsmitteln."
        )
        out = _format(raw)
        assert "…" in out
        body = out[:out.rindex(" [")] if " [" in out else out
        last_word = body.rstrip(" …").rsplit(" ", 1)[-1]
        # Last word must contain a lowercase letter (content), not be
        # all uppercase (marker).
        assert any(c.islower() for c in last_word), (
            f"Last word {last_word!r} looks like an over-stripped marker"
        )

    def test_german_abbreviation_still_dropped(self) -> None:
        # Round 19's "bzw." drop must continue to work.
        raw = (
            "Wegen Bauarbeiten zwischen A und B von 10.07.2026 bis "
            "27.07.2026 die IC-Züge mit geänderten Fahrzeiten bzw. "
            "vorverlegten Abfahrtszeiten."
        )
        out = _format(raw)
        assert "bzw. …" not in out

    def test_unit_token_uhr_dropped(self) -> None:
        # Round 20's "Uhr" drop must continue to work.
        raw = "x " * 90 + "08:45 Uhr - 14:45 Uhr - 22:00"
        out = _format(raw)
        assert "Uhr …" not in out

    def test_numeric_ordinal_dropped(self) -> None:
        # Round 22's "3." drop must continue to work.
        raw = "x " * 90 + "Dienstag, 3. März 2026"
        out = _format(raw)
        assert "3. …" not in out
