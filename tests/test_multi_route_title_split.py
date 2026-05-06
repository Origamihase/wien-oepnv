"""Regression tests for Bug Z2 (multi-route titles with " / " separator).

ÖBB titles regularly bundle several routes into a single title using
``" / "`` as the separator, e.g.::

    "Wien Praterstern ↔ Wien Meidling / Wien Hauptbahnhof ↔ Wien Hütteldorf"

The original title parser only split on ``↔`` and treated the whole
title as one chain ``A ↔ B / C ↔ D ↔ E`` — pairing inner endpoints
across the slash and producing frankenstring routes such as::

    ('Wien Meidling / Wien', 'Wien Hütteldorf')

The fix pre-splits the title on whitespace-bounded ``/`` separators
before iterating arrow pairs, so each route is parsed in isolation.
Compound names that contain a slash without surrounding spaces
(``"Bruck/Leitha"``, ``"Linz/Donau"``) stay intact because the split
requires whitespace on both sides.
"""

from __future__ import annotations

from src.providers.oebb import _extract_routes, _is_relevant


class TestMultiRouteTitleSplit:
    def test_two_routes_separated_by_whitespace_slash(self) -> None:
        title = (
            "Wien Praterstern ↔ Wien Meidling / Wien Hauptbahnhof ↔ "
            "Wien Hütteldorf"
        )
        routes = _extract_routes(title, "")
        # Both pairs must be detected, no frankenstrings.
        assert ("Wien Praterstern", "Wien Meidling") in routes
        # The "Wien Hauptbahnhof" gets normalized to "Wien" by Bahnhof-trim.
        assert ("Wien", "Wien Hütteldorf") in routes
        # No frankenstring endpoints
        all_endpoints = {ep for r in routes for ep in r}
        assert "Wien Meidling / Wien" not in all_endpoints
        assert "Wien Meidling / Wien Hauptbahnhof" not in all_endpoints

    def test_compound_slash_name_stays_intact(self) -> None:
        # "Bruck/Leitha" has no whitespace around the slash and must not
        # be split.
        routes = _extract_routes("Wien Hbf ↔ Bruck/Leitha", "")
        assert routes == [("Wien", "Bruck/Leitha")]

    def test_three_routes_with_slash_separator(self) -> None:
        title = (
            "Wien Hbf ↔ Mödling / Wien Hbf ↔ Baden / Wien Hbf ↔ "
            "Wiener Neustadt"
        )
        routes = _extract_routes(title, "")
        ep_pairs = {tuple(sorted(r)) for r in routes}
        assert ("Mödling", "Wien") in ep_pairs
        assert ("Baden", "Wien") in ep_pairs
        assert ("Wien", "Wiener Neustadt") in ep_pairs

    def test_relevant_passes_with_multi_route_title(self) -> None:
        # All routes are Wien↔Pendler — message must keep.
        title = (
            "Wien Hauptbahnhof ↔ Götzendorf / Wien Hauptbahnhof ↔ "
            "Gramatneusiedl"
        )
        assert _is_relevant(title, "") is True

    def test_single_route_unchanged(self) -> None:
        # Plain single-route title must continue to work.
        routes = _extract_routes("Wien Hbf ↔ Mödling", "")
        assert routes == [("Wien", "Mödling")]

    def test_compound_donau_name_stays_intact(self) -> None:
        # Linz/Donau is the canonical Bahnhof name — slash without
        # whitespace must not split.
        routes = _extract_routes("Wien Hbf ↔ Linz/Donau", "")
        assert ("Wien", "Linz/Donau") in routes
