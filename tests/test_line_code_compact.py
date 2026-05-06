"""Regression tests for Bug 14A (Wiener-Linien line codes split into pieces).

The HTML-to-text helper applied ``\\d → \\d ' '`` unconditionally before
any letter, so concatenated tokens like ``11A`` (Wiener-Linien bus
line) were rendered as ``11 A``. This propagated all the way to the
feed: descriptions read ``Linie 11 A: Unregelmäßige Intervalle …``
where Vienna's own naming convention uses the compact form ``11A``.

Cache item #25 (``Linie 11A: Verkehrsüberlastung``) showed the bug
directly when surfaced through the feed pipeline:

    docs/feed.xml: <description>Linie 11 A: Unregelmäßige Intervalle…

The fix tightens ``_DIGIT_ALPHA_RE`` to skip a single trailing
uppercase letter (the Wiener-Linien line-code suffix) while still
splitting multi-character unit words like ``12Uhr → 12 Uhr`` and
``20kg → 20 kg``.
"""

from __future__ import annotations

import pytest

from src.utils.text import html_to_text


class TestLineCodesStayCompact:
    @pytest.mark.parametrize(
        "code",
        ["10A", "11A", "12A", "27A", "5B", "13A", "26A", "41E", "62", "9", "U6", "S50"],
    )
    def test_compact_line_code_unchanged(self, code: str) -> None:
        assert html_to_text(code) == code

    def test_linie_prefix_keeps_compact_code(self) -> None:
        # Real feed pattern: "Linie 11A: Verkehrsüberlastung". The
        # space between the digit and the suffix-letter would have
        # been inserted by the buggy regex, breaking the convention.
        assert (
            html_to_text("Linie 11A: Verkehrsüberlastung")
            == "Linie 11A: Verkehrsüberlastung"
        )

    def test_linie_prefix_keeps_5b_compact(self) -> None:
        assert (
            html_to_text("Linie 5B: Polizeieinsatz")
            == "Linie 5B: Polizeieinsatz"
        )

    def test_real_feed_description_kept_intact(self) -> None:
        # Reproduction of the exact phrasing that surfaced in the feed
        # before the fix.
        text = (
            "Linie 11A: Unregelmäßige Intervalle in beiden Richtungen. "
            "Grund: Verkehrsüberlastung."
        )
        assert html_to_text(text) == text


class TestUnitsStillSplit:
    @pytest.mark.parametrize(
        "before,after",
        [
            ("12Uhr", "12 Uhr"),
            ("20kg", "20 kg"),
            ("2m", "2 m"),
            ("100km", "100 km"),
            ("5min", "5 min"),
            # Multi-letter capitalised word still splits.
            ("12Mio", "12 Mio"),
        ],
    )
    def test_unit_words_split(self, before: str, after: str) -> None:
        assert html_to_text(before) == after


class TestEdgeCases:
    def test_multi_digit_with_letter_suffix(self) -> None:
        # ÖBB regional service codes like ``REX 51`` arrive
        # whitespace-separated; the digit-alpha rule never fires.
        # Compact ``REX51`` stays compact.
        assert html_to_text("REX51") == "REX51"

    def test_year_followed_by_text(self) -> None:
        # Year-followed-by-word still splits via the lowercase-first
        # branch.
        assert html_to_text("2026um") == "2026 um"

    def test_year_followed_by_capital_word_splits(self) -> None:
        assert html_to_text("2026Wien") == "2026 Wien"

    def test_year_followed_by_single_capital_kept(self) -> None:
        # Exotic but consistent: year-letter combinations like ``2026A``
        # would be rare in real data, but the rule treats them as
        # line-code-like and keeps them compact. This is fine — no real
        # disambiguation case observed in cache.
        assert html_to_text("2026A") == "2026A"
