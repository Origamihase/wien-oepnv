"""Regression tests for Bug 13A (WL cache items keep stale newline titles).

Audit round 12 fixed ``_tidy_title_wl`` so it collapses any whitespace
run including isolated ``\\n``/``\\t``. But the WL cache is only
refreshed periodically; cached items stored before the fix continued
to surface in the feed with embedded newlines until the next refresh.

The fix mirrors the existing ``_post_filter_oebb`` pattern: a defence-
in-depth normalisation pass that re-applies ``\\s+ → ' '`` to titles
loaded from cache. Items without a string title pass through
untouched.
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.build_feed import _post_filter_wl


class TestPostFilterWlNormalisesTitles:
    def test_single_newline_collapsed(self) -> None:
        items: List[Dict[str, Any]] = [
            {"title": "41E/10A: Ersatzbus 41E\nhält beim 10A"}
        ]
        out = _post_filter_wl(items)
        assert out[0]["title"] == "41E/10A: Ersatzbus 41E hält beim 10A"

    def test_multiple_newlines_collapsed(self) -> None:
        items: List[Dict[str, Any]] = [
            {
                "title": (
                    "41E/200: Ersatzbus 41E\nhalten bei\nWähringer Str 200"
                )
            }
        ]
        out = _post_filter_wl(items)
        assert out[0]["title"] == (
            "41E/200: Ersatzbus 41E halten bei Währinger Str 200"
        )

    def test_carriage_return_and_tab_collapsed(self) -> None:
        items: List[Dict[str, Any]] = [{"title": "Foo\r\nBar\tBaz"}]
        out = _post_filter_wl(items)
        assert out[0]["title"] == "Foo Bar Baz"

    def test_clean_title_unchanged(self) -> None:
        items: List[Dict[str, Any]] = [{"title": "U6: Störung Praterstern"}]
        out = _post_filter_wl(items)
        assert out[0]["title"] == "U6: Störung Praterstern"

    def test_other_fields_preserved(self) -> None:
        items: List[Dict[str, Any]] = [
            {
                "title": "Foo\nBar",
                "description": "preserved\nas\nis",
                "guid": "wl-1",
                "pubDate": "2026-05-06T12:00:00Z",
            }
        ]
        out = _post_filter_wl(items)
        # Title cleaned, but other fields untouched.
        assert out[0]["title"] == "Foo Bar"
        assert out[0]["description"] == "preserved\nas\nis"
        assert out[0]["guid"] == "wl-1"

    def test_input_dict_not_mutated(self) -> None:
        # Defence: the post-filter must not modify the caller's dict.
        original = {"title": "Foo\nBar"}
        items: List[Dict[str, Any]] = [original]
        _post_filter_wl(items)
        assert original["title"] == "Foo\nBar"  # unchanged


class TestPostFilterWlPassesThrough:
    def test_non_dict_items_pass_through(self) -> None:
        items: List[Any] = ["not-a-dict", 42, None]
        assert _post_filter_wl(items) == items

    def test_dict_without_title_passes_through(self) -> None:
        items: List[Dict[str, Any]] = [{"foo": "bar"}]
        out = _post_filter_wl(items)
        assert out == items

    def test_dict_with_none_title_passes_through(self) -> None:
        items: List[Dict[str, Any]] = [{"title": None}]
        out = _post_filter_wl(items)
        assert out == items

    def test_dict_with_empty_title_passes_through(self) -> None:
        items: List[Dict[str, Any]] = [{"title": ""}]
        out = _post_filter_wl(items)
        assert out == items

    def test_empty_list(self) -> None:
        assert _post_filter_wl([]) == []
