"""Bug b7: ``_normalize_name`` strips all digits before tokenising, so two
titles that differ ONLY by a platform/track number ("Bahnsteig 1" vs
"Bahnsteig 5") collapse to identical tokens and wrongly merge into one feed
item. ``_platform_numbers`` captures the platform-pinned digits so a merge is
blocked when both titles name different, non-overlapping platforms — while a
same-event pair that merely carries different DATE numbers ("ab 03./10.
November") still merges, because dates are not captured.
"""

from typing import Any

from src.feed.merge import _platform_numbers, deduplicate_fuzzy


def test_platform_numbers_extracts_only_platform_digits() -> None:
    assert _platform_numbers("Bahnsteig 1 Wien Mitte") == frozenset({"1"})
    assert _platform_numbers("Gleis 3 und Gleis 7") == frozenset({"3", "7"})
    # Dates and line numbers are NOT platform numbers.
    assert _platform_numbers("Umleitung ab 03. November") == frozenset()
    assert _platform_numbers("Linie 60A Sperre") == frozenset()


def test_distinct_platform_numbers_block_merge() -> None:
    # Same line + station, different platforms → distinct incidents.
    items: list[dict[str, Any]] = [
        {"guid": "a", "_identity": "x|a", "title": "S1: Bahnsteig 1 Wien Mitte"},
        {"guid": "b", "_identity": "x|b", "title": "S1: Bahnsteig 5 Wien Mitte"},
    ]
    assert len(deduplicate_fuzzy(items)) == 2


def test_same_platform_still_merges() -> None:
    items: list[dict[str, Any]] = [
        {
            "guid": "a",
            "_identity": "x|a",
            "title": "S1: Bahnsteig 1 Wien Mitte",
            "description": "Erste Meldung.",
        },
        {
            "guid": "b",
            "_identity": "x|b",
            "title": "S1: Bahnsteig 1 Wien Mitte",
            "description": "Zweite Meldung.",
        },
    ]
    assert len(deduplicate_fuzzy(items)) == 1


def test_date_variant_in_title_still_merges() -> None:
    # Regression guard: differing DATE numbers in the title (not platforms)
    # must NOT block the merge of the same incident reported twice.
    items: list[dict[str, Any]] = [
        {
            "guid": "a",
            "_identity": "x|a",
            "title": "S80: Umleitung ab 03. November Wien Praterstern",
        },
        {
            "guid": "b",
            "_identity": "x|b",
            "title": "S80: Umleitung ab 10. November Wien Praterstern",
        },
    ]
    assert len(deduplicate_fuzzy(items)) == 1
