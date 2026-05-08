"""Regression test for Bug 29A (WL Störung items without a line code).

Real WL Störung items occasionally arrive from the OGD API without
any ``relatedLines`` AND without a line code anywhere in the title
text. The fallback ``_detect_line_pairs_from_text`` then produces an
empty line set, ``_ensure_line_prefix`` is a no-op, and the final
title surfaces as e.g.:

    "Verkehrsunfall Betrieb ab Nordbrücke"
    "Fahrtbehinderung wegen Verkehrsunfall"

The user has no way to tell which line is affected, so the meldung
is unusable in a transit feed.

The fix extends ``_post_filter_wl`` to drop WL Störung items whose
title doesn't start with a line-prefix pattern (``U6:``, ``41E:``,
``9/40/41/42:``).
"""

from __future__ import annotations

from typing import Any

from src.build_feed import _post_filter_wl


class TestStoerungWithoutLinePrefixDropped:
    def test_verkehrsunfall_no_line_dropped(self) -> None:
        items: list[dict[str, Any]] = [
            {
                "title": "Verkehrsunfall Betrieb ab Nordbrücke",
                "category": "Störung",
            }
        ]
        out = _post_filter_wl(items)
        assert out == []

    def test_fahrtbehinderung_no_line_dropped(self) -> None:
        items: list[dict[str, Any]] = [
            {
                "title": "Fahrtbehinderung wegen Verkehrsunfall",
                "category": "Störung",
            }
        ]
        out = _post_filter_wl(items)
        assert out == []

    def test_with_line_prefix_kept(self) -> None:
        items: list[dict[str, Any]] = [
            {
                "title": "26: Fahrtbehinderung Verkehrsunfall",
                "category": "Störung",
            }
        ]
        out = _post_filter_wl(items)
        assert len(out) == 1

    def test_compound_line_prefix_kept(self) -> None:
        items: list[dict[str, Any]] = [
            {
                "title": "9/40/41/42: Gleisbauarbeiten",
                "category": "Störung",
            }
        ]
        out = _post_filter_wl(items)
        assert len(out) == 1

    def test_u_bahn_prefix_kept(self) -> None:
        items: list[dict[str, Any]] = [
            {
                "title": "U6: Verspätung wegen Schadhaftem Fahrzeug",
                "category": "Störung",
            }
        ]
        out = _post_filter_wl(items)
        assert len(out) == 1

    def test_hinweis_without_prefix_kept(self) -> None:
        # Only Störung is dropped — Hinweis items can have unusual
        # title shapes without a line prefix and should be left alone.
        items: list[dict[str, Any]] = [
            {
                "title": "Sonderbetrieb Wings for Life Run",
                "category": "Hinweis",
            }
        ]
        out = _post_filter_wl(items)
        assert len(out) == 1
