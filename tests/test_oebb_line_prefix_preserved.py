"""Regression tests for Bug 17A (line prefix swallowed by Vienna-first reorder).

``_clean_title_keep_places`` runs an "endpoint reorder" step that puts
the Vienna station first when only one of the two split parts resolves
against the directory. Before the fix, an ÖBB title like::

    "S40: Wien Franz-Josefs-Bahnhof ↔ Wien Heiligenstadt"

resulted in::

    "Wien Heiligenstadt ↔ S40: Wien Franz-Josefs-"

because:

1. ``ARROW_ANY_RE`` split into ``["S40: Wien Franz-Josefs-Bahnhof",
   "Wien Heiligenstadt"]``.
2. ``is_in_vienna("S40: Wien Franz-Josefs-Bahnhof")`` returned False —
   the line prefix breaks the ``station_info`` lookup.
3. ``is_in_vienna("Wien Heiligenstadt")`` returned True.
4. The reorder swapped the parts; the line prefix ended up in the
   middle of the title.

The downstream ``_format_route_title`` rebuild masked the user-visible
breakage in the most common case, but the corrupted intermediate
leaked through whenever ``_extract_routes`` returned no Wien-relevant
routes (e.g. for single-station fallthrough).

The fix splits the leading line marker off at the very start of
``_clean_title_keep_places`` and re-attaches it after the reorder
runs. The reorder no longer sees the prefix at all.
"""

from __future__ import annotations

from src.providers.oebb import _clean_title_keep_places, _extract_line_prefix


class TestLinePrefixPreservedThroughCleanup:
    def test_s40_prefix_kept_no_reorder(self) -> None:
        raw = "S40: Wien Franz-Josefs-Bahnhof ↔ Wien Heiligenstadt"
        out = _clean_title_keep_places(raw)
        # Line prefix on the LEFT, original endpoint order preserved.
        assert out.startswith("S40:")
        assert "Wien Franz-Josefs-Bahnhof ↔ Wien Heiligenstadt" in out
        # And nowhere does the prefix end up in the middle.
        assert " S40:" not in out
        assert "↔ S40" not in out

    def test_rex7_prefix_kept(self) -> None:
        raw = "REX 7: Wien Floridsdorf ↔ Flughafen Wien"
        assert _clean_title_keep_places(raw).startswith("REX 7:")

    def test_u6_prefix_kept_with_canonical_expansion(self) -> None:
        # U6: Heiligenstadt ↔ Floridsdorf — the canonical-name expansion
        # turns "Heiligenstadt" into "Wien Heiligenstadt"; the prefix
        # must still survive at the start.
        out = _clean_title_keep_places("U6: Heiligenstadt ↔ Floridsdorf")
        assert out.startswith("U6:")
        assert "Wien Heiligenstadt" in out

    def test_rjx_prefix_kept(self) -> None:
        raw = "RJX 12: Wien Hauptbahnhof ↔ Salzburg"
        assert _clean_title_keep_places(raw).startswith("RJX 12:")

    def test_s_bahn_long_form_prefix_kept(self) -> None:
        # The verbose "S-Bahn" form (now that round-13 added its line-
        # parser support) must also survive the cleanup.
        raw = "S-Bahn 50: Wien Westbahnhof ↔ St. Pölten"
        out = _clean_title_keep_places(raw)
        assert out.startswith("S-Bahn 50:")


class TestExtractLinePrefixRoundTrip:
    def test_extract_after_cleanup_returns_prefix(self) -> None:
        # The downstream pipeline calls ``_extract_line_prefix`` on the
        # cleaned title. The prefix must round-trip cleanly so that
        # ``_format_route_title`` re-attaches it on the rebuild.
        cleaned = _clean_title_keep_places(
            "S40: Wien Franz-Josefs-Bahnhof ↔ Wien Heiligenstadt"
        )
        prefix, rest = _extract_line_prefix(cleaned)
        assert prefix == "S40"
        assert "Wien Franz-Josefs-Bahnhof" in rest
        assert "Wien Heiligenstadt" in rest

    def test_extract_after_cleanup_for_combo_with_category(self) -> None:
        # "S 50: Bauarbeiten: Wien Westbf …" combines a line prefix with
        # an inner category prefix. Only "S 50" should remain at the
        # front; "Bauarbeiten:" is stripped by the segment iteration.
        cleaned = _clean_title_keep_places(
            "S 50: Bauarbeiten: Wien Westbf Wien Hütteldorf/Tullnerbach-Pressbaum"
        )
        prefix, _ = _extract_line_prefix(cleaned)
        assert prefix == "S 50"
        assert "Bauarbeiten" not in cleaned


class TestNoPrefixNoChange:
    def test_no_line_prefix_unchanged(self) -> None:
        # A title without a line prefix must still pass through.
        raw = "Wien Hauptbahnhof ↔ Mödling"
        assert _clean_title_keep_places(raw) == "Wien Hauptbahnhof ↔ Mödling"
