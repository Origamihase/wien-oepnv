"""Regression tests for Bug 30A (multi-route titles collapsed into a chain).

User feedback: an ÖBB corridor disruption surfaced as the redundant
title::

    U3: Wien Westbahnhof ↔ Wien Hütteldorf
        / Wien Hütteldorf ↔ Tullnerbach-Pressbaum
        / Wien Westbahnhof ↔ St. Pölten Hauptbahnhof

Two issues:

1. The ``U3:`` prefix is *wrong*: the disrupted line is ``S 50`` (the
   description's ``keine S 50-Züge fahren`` clause), but the previous
   ``_LINE_TOKEN_RE`` matched the FIRST line code in the description —
   which happened to be ``U3`` mentioned in the alternative-routes
   block (``U3: Wien Ottakring <=> Hütteldorfer Straße ...``).

2. The ``/ ``-separated multi-route title is overloaded: the three
   segments are a single corridor with ``Wien Westbahnhof`` and ``Wien
   Hütteldorf`` as inner hubs. A clean chain reads::

       S 50: St. Pölten Hauptbahnhof ↔ Wien Westbahnhof
            ↔ Wien Hütteldorf ↔ Tullnerbach-Pressbaum

The fixes:

* ``_LINE_TOKEN_RE`` now requires a ``-Züge``/``Zug`` follower so it
  only matches affected lines (not alternative-route headings).
* When the description identifies an affected line that disagrees
  with the cached title prefix, the description wins.
* ``_format_route_title`` builds an undirected graph from the
  resolved routes and renders a linear chain when one is possible
  (every node degree ≤ 2, exactly two endpoints of degree 1, single
  connected component). Otherwise the old ``/`` form remains.
* The chain's start endpoint prefers Wien (when one endpoint is in
  Wien) so the Wien-focused feed leads with the Wien name; otherwise
  the alphabetically smaller endpoint goes first.
* The line marker is canonicalised to ``LETTERS DIGITS`` form so
  ``REX7`` from the description renders as ``REX 7`` in the title.
"""

from __future__ import annotations

from src.providers.oebb import (
    _apply_route_title,
    _format_route_title,
    _normalize_line_token,
    _try_chain_routes,
)


class TestLineTokenNormalisation:
    def test_concatenated_to_spaced(self) -> None:
        assert _normalize_line_token("REX7") == "REX 7"
        assert _normalize_line_token("S50") == "S 50"

    def test_already_spaced_unchanged(self) -> None:
        assert _normalize_line_token("REX 7") == "REX 7"
        assert _normalize_line_token("S 50") == "S 50"

    def test_multiple_internal_whitespace_collapsed(self) -> None:
        assert _normalize_line_token("REX   7") == "REX 7"

    def test_s_bahn_prefix_preserved(self) -> None:
        # The "S-Bahn 50" verbose form keeps its hyphen.
        assert _normalize_line_token("S-Bahn50") == "S-Bahn 50"

    def test_strip_outer_whitespace(self) -> None:
        assert _normalize_line_token("  S 50  ") == "S 50"


class TestChainRouteCollapse:
    def test_three_segment_corridor_chains(self) -> None:
        # The exact reproduction from the user-reported cache item.
        routes = [
            ("Wien Westbahnhof", "Wien Hütteldorf"),
            ("Wien Hütteldorf", "Tullnerbach-Pressbaum"),
            ("Wien Westbahnhof", "St. Pölten Hauptbahnhof"),
        ]
        chain = _try_chain_routes(routes)
        assert chain is not None
        # The chain must visit each station exactly once.
        assert sorted(chain) == sorted(
            ["Wien Westbahnhof", "Wien Hütteldorf",
             "Tullnerbach-Pressbaum", "St. Pölten Hauptbahnhof"]
        )
        # Connected stations are adjacent in the chain.
        pairs = {tuple(sorted(p)) for p in zip(chain, chain[1:], strict=False)}
        for a, b in routes:
            assert tuple(sorted((a, b))) in pairs

    def test_two_segments_share_endpoint(self) -> None:
        routes = [
            ("Wien Hbf", "Wien Meidling"),
            ("Wien Meidling", "Mödling"),
        ]
        chain = _try_chain_routes(routes)
        assert chain is not None
        assert chain == ["Wien Hbf", "Wien Meidling", "Mödling"]

    def test_disjoint_routes_not_chained(self) -> None:
        # Two routes that share no endpoint must NOT collapse — that
        # would silently merge two unrelated disruptions.
        routes = [
            ("Wien Hbf", "Mödling"),
            ("Wien Floridsdorf", "Wien Praterstern"),
        ]
        assert _try_chain_routes(routes) is None

    def test_branching_hub_not_chained(self) -> None:
        # Three routes all sharing one hub form a star, not a chain.
        routes = [
            ("Wien Hbf", "Mödling"),
            ("Wien Hbf", "Baden"),
            ("Wien Hbf", "Wiener Neustadt"),
        ]
        assert _try_chain_routes(routes) is None

    def test_cycle_not_chained(self) -> None:
        routes = [
            ("A", "B"),
            ("B", "C"),
            ("C", "A"),
        ]
        assert _try_chain_routes(routes) is None

    def test_self_loop_rejected(self) -> None:
        assert _try_chain_routes([("A", "A")]) is None

    def test_single_route_passes_through(self) -> None:
        # A single segment is trivially a 2-node chain.
        chain = _try_chain_routes([("Wien Hbf", "Mödling")])
        assert chain == ["Wien Hbf", "Mödling"] or chain == ["Mödling", "Wien Hbf"]


class TestFormatRouteTitleChain:
    def test_corridor_renders_as_chain(self) -> None:
        # User's exact requested output.
        routes = [
            ("Wien Westbahnhof", "Wien Hütteldorf"),
            ("Wien Hütteldorf", "Tullnerbach-Pressbaum"),
            ("Wien Westbahnhof", "St. Pölten Hauptbahnhof"),
        ]
        out = _format_route_title(routes, "S 50")
        assert out == (
            "S 50: St. Pölten Hauptbahnhof ↔ Wien Westbahnhof"
            " ↔ Wien Hütteldorf ↔ Tullnerbach-Pressbaum"
        )

    def test_disjoint_routes_keep_slash_format(self) -> None:
        routes = [
            ("Wien Hbf", "Mödling"),
            ("Wien Floridsdorf", "Wien Praterstern"),
        ]
        out = _format_route_title(routes)
        assert " / " in out
        # The chain-collapse must NOT activate for disjoint routes.

    def test_single_route_unchanged(self) -> None:
        out = _format_route_title([("Wien Hbf", "Mödling")])
        # Vienna-first orientation preserved; Hbf expands to Hauptbahnhof
        # via ``_expand_station_abbreviations`` for readability.
        assert out == "Wien Hauptbahnhof ↔ Mödling"


class TestApplyRouteTitleEndToEnd:
    def test_alternative_line_not_picked_as_prefix(self) -> None:
        # Cache item #13 reproduction. The description names
        # alternative U-Bahn routes (``U3: Wien Ottakring <=> …``)
        # BEFORE the actual affected lines (``keine S 50-Züge``).
        # The previous logic prepended ``U3:`` as the title prefix.
        title = (
            "U3: Wien Westbahnhof ↔ Wien Hütteldorf"
            " / Wien Hütteldorf ↔ Tullnerbach-Pressbaum"
            " / Wien Westbahnhof ↔ St. Pölten Hauptbahnhof"
        )
        desc = (
            "Wegen Bauarbeiten können von 03.06.2026 (23:00 Uhr) bis "
            "08.06.2026 (03:00 Uhr) zwischen Wien Westbahnhof (U) und "
            "Wien Hütteldorf Bahnhof (U) keine Nahverkehrszüge fahren. "
            "Reisende mit gültigem Ticket haben die Möglichkeit "
            "folgende Alternativverbindungen der WIENER LINIEN zu "
            "nutzen:\n"
            "U3: Wien Ottakring <=> Hütteldorfer Straße <=> Wien Westbahnhof\n"
            "U4: Wien Hütteldorf <=> Hietzing\n"
            "Zwischen Wien Hütteldorf Bahnhof und Tullnerbach-Pressbaum "
            "Bahnhof können keine S 50-Züge fahren.\n"
            "Zwischen Wien Westbahnhof (U) und St.Pölten Hbf können "
            "keine REX 50-Züge fahren."
        )
        out = _apply_route_title(title, desc)
        # The wrong U3 prefix must be gone, replaced by the actual S 50.
        assert "U3:" not in out
        assert out.startswith("S 50:")
        # And the multi-route body collapses into a single chain.
        # Three "↔" separators for a 4-node chain.
        assert out.count("↔") == 3
        assert " / " not in out

    def test_normalisation_preserves_correct_existing_prefix(self) -> None:
        # When the cached title's prefix already agrees with the
        # description-detected line (modulo whitespace normalisation),
        # the rendered prefix uses the canonical form.
        title = "REX 7: Wien Floridsdorf ↔ Flughafen Wien"
        desc = (
            "Wegen Bauarbeiten zwischen Wien Floridsdorf Bahnhof (U) "
            "und Flughafen Wien Bahnhof können einige REX7-Züge nicht "
            "fahren."
        )
        out = _apply_route_title(title, desc)
        assert out.startswith("REX 7: ")
        assert "REX7:" not in out  # canonicalised away
