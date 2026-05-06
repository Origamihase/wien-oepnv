"""Regression tests for Bug 11C (route dedup misses whitespace variants).

Real ÖBB descriptions sometimes carry the same station name in two
spellings — the title may use ``St. Pölten Hauptbahnhof`` while the
description writes ``St.Pölten Hbf`` (no space after the period). The
upstream extraction in ``_extract_routes`` keys on raw casefold text, so
both spellings produce different keys and both survive into
``_format_route_title``. The formatted title then repeats the route::

    "S 50: Wien Hütteldorf ↔ Tullnerbach-Pressbaum
        / Wien Westbahnhof ↔ St. Pölten Hauptbahnhof
        / Wien Westbahnhof ↔ Wien Hütteldorf
        / Wien Westbahnhof ↔ St. Pölten Hauptbahnhof"   ← duplicate

(The duplicate is from the cached event #12 in
``cache/oebb_c40d21/events.json``.)

The fix deduplicates routes inside ``_format_route_title`` based on the
*canonical* endpoint pair (after ``station_info`` resolution and
``(VOR)``-suffix stripping) so whitespace variants of the same station
collapse correctly.
"""

from __future__ import annotations

from src.providers.oebb import _format_route_title


class TestRouteCanonicalDedup:
    def test_st_poelten_with_and_without_space_dedups(self) -> None:
        # Two raw spellings of the same destination — title must show
        # only one Wien Westbahnhof ↔ St. Pölten line.
        routes = [
            ("Wien Westbahnhof", "St. Pölten"),
            ("Wien Westbahnhof", "St.Pölten"),
        ]
        title = _format_route_title(routes)
        # Exactly one occurrence of the route after dedup.
        assert title.count("↔") == 1
        assert "St. Pölten Hauptbahnhof" in title

    def test_real_cache_item_12_no_duplicate(self) -> None:
        # Reproduction of the cache item: 4 raw routes, one of which is a
        # whitespace duplicate of another. Output must have 3 routes.
        routes = [
            ("Wien Hütteldorf", "Tullnerbach-Pressbaum"),
            ("Wien Westbahnhof", "St. Pölten"),
            ("Wien Westbahnhof", "Wien Hütteldorf"),
            ("Wien Westbahnhof", "St.Pölten"),
        ]
        title = _format_route_title(routes, "S 50")
        # 3 unique routes → 3 ↔ separators
        assert title.count("↔") == 3
        # ensure the duplicate is gone
        assert title.count("St. Pölten Hauptbahnhof") == 1

    def test_distinct_routes_kept(self) -> None:
        # Defence: when routes are genuinely different, all are kept.
        routes = [
            ("Wien Hbf", "Mödling"),
            ("Wien Hbf", "Baden"),
            ("Wien Hbf", "Wiener Neustadt"),
        ]
        title = _format_route_title(routes)
        assert title.count("↔") == 3

    def test_orientation_swap_does_not_create_duplicate(self) -> None:
        # The Vienna-first orientation already canonicalises A/B order, so
        # ("Wien", "Mödling") and ("Mödling", "Wien") must dedup to one
        # route.
        routes = [
            ("Wien Hbf", "Mödling"),
            ("Mödling", "Wien Hbf"),
        ]
        title = _format_route_title(routes)
        assert title.count("↔") == 1


class TestRouteDedupCompound:
    def test_compound_franz_josefs_route_renders_correctly(self) -> None:
        # Bug 11A interaction: the compound proper noun must render
        # without the dangling-hyphen artefact.
        routes = [("Wien Franz-Josefs-Bahnhof", "Wien Heiligenstadt")]
        title = _format_route_title(routes, "S40")
        assert title == "S40: Wien Franz-Josefs-Bahnhof ↔ Wien Heiligenstadt"
        assert "Franz-Josefs-Bahnhof" in title
        assert "Franz-Josefs- " not in title  # no dangling-hyphen artefact
