"""Defence-in-depth: re-apply the ÖBB relevance filter to cache items.

Audit-round-5 finding (bug P). The ÖBB cache is only refreshed by the
`update-oebb-cache.yml` workflow, so a filter improvement does not reach
the feed until the next cache refresh — meanwhile the live feed can
carry items that the *current* spec considers irrelevant. The post-
filter in `build_feed.read_cache_oebb` re-runs `_is_relevant` against
each cached item so the feed always reflects the latest filter state.
"""

from __future__ import annotations

from typing import Any, List

from src.build_feed import _post_filter_oebb


class TestPostFilterDropsStaleCacheItems:
    def test_passes_real_wien_pendler_route(self) -> None:
        items: List[Any] = [
            {
                "title": "Wien Hauptbahnhof ↔ Mödling",
                "description": "Bauarbeiten zwischen Wien Hbf und Mödling.",
            }
        ]
        result = _post_filter_oebb(items)
        assert len(result) == 1

    def test_drops_wien_distant_routes(self) -> None:
        items: List[Any] = [
            {
                "title": "Bauarbeiten: Wien/München Roma Termini",
                "description": "NJ-Züge umgeleitet via Salzburg.",
            }
        ]
        assert _post_filter_oebb(items) == []

    def test_drops_pendler_distant_route(self) -> None:
        items: List[Any] = [
            {
                "title": "Bauarbeiten: Wiener Neustadt Hauptbahnhof Semmering",
                "description": "Wegen Bauarbeiten werden Fernverkehrszüge umgeleitet.",
            }
        ]
        assert _post_filter_oebb(items) == []

    def test_drops_facility_only_with_wien_mention(self) -> None:
        items: List[Any] = [
            {"title": "Aufzug defekt: Wien Hauptbahnhof", "description": "x"}
        ]
        assert _post_filter_oebb(items) == []

    def test_drops_weather_only_with_wien_mention(self) -> None:
        items: List[Any] = [
            {
                "title": "Sturm im Raum Wien",
                "description": "Verzögerungen bei der S-Bahn Wien.",
            }
        ]
        assert _post_filter_oebb(items) == []


class TestPostFilterPreservesGenericItems:
    def test_passes_through_items_without_title(self) -> None:
        # Test fixtures and metadata items shouldn't be dropped.
        items: List[Any] = [{"provider": "oebb"}, {"foo": "bar"}]
        assert _post_filter_oebb(items) == items

    def test_passes_through_non_dict_items(self) -> None:
        items: List[Any] = ["not-a-dict", 42]
        assert _post_filter_oebb(items) == items
