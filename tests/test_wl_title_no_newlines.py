"""Regression tests for Bug 12A (newlines preserved in WL feed titles).

Real Wiener-Linien API responses sometimes carry titles like::

    "Ersatzbus 41E\\nhält beim 10A"
    "Ersatzbus 41E\\nhalten bei\\nWähringer Str 200"

The previous ``_tidy_title_wl`` and ``_normalize_whitespace`` collapsed
only ``\\s{2,}`` runs, so a single ``\\n`` between words survived
verbatim. RSS/Atom titles must be single-line, and the cached items
in ``cache/wl_9d709a/events.json`` showed the artefact directly.

The fix tightens both helpers to ``\\s+`` so any whitespace run —
including isolated newlines or tabs — collapses to a single space.
"""

from __future__ import annotations

from src.providers.wl_fetch import _normalize_whitespace
from src.providers.wl_text import _tidy_title_wl


class TestTidyTitleWlSingleLine:
    def test_single_newline_collapsed(self) -> None:
        assert _tidy_title_wl("Ersatzbus 41E\nhält beim 10A") == (
            "Ersatzbus 41E hält beim 10A"
        )

    def test_two_newlines_collapsed(self) -> None:
        assert _tidy_title_wl(
            "Ersatzbus 41E\nhalten bei\nWähringer Str 200"
        ) == "Ersatzbus 41E halten bei Währinger Str 200"

    def test_carriage_return_collapsed(self) -> None:
        assert _tidy_title_wl("Foo\r\nBar") == "Foo Bar"

    def test_tab_collapsed(self) -> None:
        assert _tidy_title_wl("Foo\tBar") == "Foo Bar"

    def test_normal_title_unchanged(self) -> None:
        # No-newline titles must keep working.
        assert _tidy_title_wl("U6: Störung Praterstern") == (
            "U6: Störung Praterstern"
        )


class TestNormalizeWhitespaceSingleLine:
    def test_normalize_whitespace_collapses_newline(self) -> None:
        assert _normalize_whitespace("foo\nbar") == "foo bar"

    def test_normalize_whitespace_collapses_tabs_and_runs(self) -> None:
        assert _normalize_whitespace("foo \t  \n bar") == "foo bar"

    def test_normalize_whitespace_empty(self) -> None:
        assert _normalize_whitespace("") == ""

    def test_normalize_whitespace_strips_outer(self) -> None:
        assert _normalize_whitespace("  foo bar  ") == "foo bar"
