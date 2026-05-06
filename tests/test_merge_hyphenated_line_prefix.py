"""Regression tests for Bug 13B (hyphenated line prefix not merged).

``_LINE_PREFIX_RE`` in ``src/feed/merge.py`` previously required a
contiguous letter group followed by digits, so the verbose spellings
``S-Bahn 50:`` and ``U-Bahn 6:`` failed to match. Items carrying those
titles silently bypassed the fuzzy-merge step, leaving two duplicate
items in the feed for the same incident reported under different
prefix styles.

The fix:

- The regex now tolerates an optional ``-Bahn`` segment between the
  letter group and the digit group.
- ``_parse_title`` strips ``-bahn`` (case-insensitive) before tokenising
  so ``S-Bahn 50`` collapses to the canonical ``S50`` token — matching
  the compact ``S 50`` / ``S50`` produced by other providers.
"""

from __future__ import annotations

from src.feed.merge import _parse_title, deduplicate_fuzzy


class TestHyphenatedLinePrefix:
    def test_s_bahn_50_recognised(self) -> None:
        lines, name = _parse_title("S-Bahn 50: Wien Westbahnhof")
        assert lines == {"S50"}
        assert name == "Wien Westbahnhof"

    def test_u_bahn_6_recognised(self) -> None:
        lines, name = _parse_title("U-Bahn 6: Heiligenstadt")
        assert lines == {"U6"}
        assert name == "Heiligenstadt"

    def test_compact_s50_unchanged(self) -> None:
        # The previous behaviour for compact prefixes must stay intact.
        lines, name = _parse_title("S 50: Wien Westbahnhof")
        assert lines == {"S50"}
        assert name == "Wien Westbahnhof"

    def test_s_bahn_collapses_to_same_token_as_s_compact(self) -> None:
        # Round-trip: both spellings now produce identical line sets,
        # which is what fuzzy-merge needs for the dedup decision.
        lines_a, _ = _parse_title("S-Bahn 50: Foo")
        lines_b, _ = _parse_title("S 50: Foo")
        assert lines_a == lines_b

    def test_multi_line_prefix_with_hyphenated(self) -> None:
        # Slash-separated prefix must still parse all tokens, hyphen-
        # form included.
        lines, _ = _parse_title("S-Bahn 50/U-Bahn 6: Bauarbeiten")
        assert lines == {"S50", "U6"}


class TestHyphenatedLinePrefixMerging:
    def test_hyphenated_and_compact_merge(self) -> None:
        items = [
            {
                "title": "S-Bahn 50: Bauarbeiten Wien Westbahnhof",
                "description": "ÖBB report.",
                "guid": "oebb_g1",
                "provider": "oebb",
                "source": "oebb",
            },
            {
                "title": "S 50: Bauarbeiten Wien Westbahnhof",
                "description": "VOR report.",
                "guid": "vor_g1",
                "provider": "vor",
                "source": "vor",
            },
        ]
        merged = deduplicate_fuzzy(items)
        # Without the hyphen-tolerance fix, the items would not match
        # and both would survive — verify exactly one item remains.
        assert len(merged) == 1
