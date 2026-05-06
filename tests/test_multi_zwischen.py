"""Regression tests for Bug Y (multi-route with "und zwischen").

ÖBB descriptions sometimes carry several ``zwischen X und Y`` clauses
joined by plain "und zwischen" or "und," — for example::

    Wegen Bauarbeiten zwischen Wien Hbf und Götzendorf, und zwischen
    Wien Hbf und Gramatneusiedl.

The previous lookahead listed only ``sowie\\s+zwischen`` as a
multi-clause boundary. Plain "und zwischen" let the non-greedy ``b``
capture span the whole sentence, so finditer surfaced just the first
route and the rest of the message slipped through unchecked.

The fix adds ``und\\s+zwischen`` and ``,\\s*und`` to the boundary
list so both real-world phrasings split correctly.
"""

from __future__ import annotations

from src.providers.oebb import _extract_routes, _is_relevant


class TestMultiRouteUndZwischen:
    def test_und_zwischen_separator_splits_correctly(self) -> None:
        routes = _extract_routes(
            "Bauarbeiten",
            "Wegen Bauarbeiten zwischen Wien Hbf und Wien Meidling und "
            "zwischen Wien Hbf und Wien Praterstern.",
        )
        assert routes == [("Wien", "Wien Meidling"), ("Wien", "Wien Praterstern")]

    def test_komma_und_zwischen_separator_splits_correctly(self) -> None:
        # Real cache pattern: comma + und + zwischen.
        routes = _extract_routes(
            "Bauarbeiten",
            "Wegen Bauarbeiten zwischen Wien Hbf und Götzendorf, und "
            "zwischen Wien Hbf und Gramatneusiedl.",
        )
        assert routes == [("Wien", "Götzendorf"), ("Wien", "Gramatneusiedl")]

    def test_sowie_zwischen_still_works(self) -> None:
        # Defence in depth: the original sowie-separator must keep working.
        routes = _extract_routes(
            "Bauarbeiten",
            "Wegen Bauarbeiten zwischen Wien Hbf und Mödling sowie "
            "zwischen Wien Hbf und Baden.",
        )
        assert routes == [("Wien", "Mödling"), ("Wien", "Baden")]

    def test_single_route_with_trailing_und(self) -> None:
        # The "und" alone (without "zwischen" right after) must NOT be a
        # boundary — otherwise endpoints get truncated.
        routes = _extract_routes(
            "Bauarbeiten",
            "zwischen Wien Hbf und Mödling sind Verspätungen zu erwarten.",
        )
        assert routes == [("Wien", "Mödling")]

    def test_three_zwischen_clauses(self) -> None:
        routes = _extract_routes(
            "Bauarbeiten",
            "zwischen Wien Hbf und Wien Meidling, und zwischen Wien Hbf "
            "und Wien Floridsdorf, und zwischen Wien Hbf und Wien Praterstern.",
        )
        assert len(routes) == 3
        assert ("Wien", "Wien Meidling") in routes
        assert ("Wien", "Wien Floridsdorf") in routes
        assert ("Wien", "Wien Praterstern") in routes

    def test_relevant_message_with_multi_pendler_routes(self) -> None:
        # All three routes are Wien↔Pendler; message must keep.
        assert (
            _is_relevant(
                "Bauarbeiten",
                "Wegen Bauarbeiten zwischen Wien Hbf und Mödling sowie "
                "zwischen Wien Hbf und Baden.",
            )
            is True
        )

    def test_drop_when_all_multi_routes_are_pendler_pendler(self) -> None:
        # All three are Pendler-Pendler — message must drop.
        assert (
            _is_relevant(
                "Bauarbeiten",
                "Wegen Bauarbeiten zwischen Mödling und Baden sowie "
                "zwischen Mödling und Wiener Neustadt.",
            )
            is False
        )
