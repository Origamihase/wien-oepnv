"""Regression tests for Bug 18A (multi-route arrows lost in title cleanup).

``_clean_title_keep_places`` runs ``ARROW_ANY_RE.split`` to break the
title into endpoints. For a chained title like ``A ↔ B / C ↔ D`` the
split produces three parts (``A``, ``B / C``, ``D``). The previous
join logic joined ``parts[0]`` and ``parts[1]`` with ↔ and then
appended the remaining parts with a *plain space*, dropping the
inner ↔ separator silently::

    raw:     "Wien Hauptbahnhof ↔ Götzendorf / Wien Hauptbahnhof ↔ Gramatneusiedl"
    cleaned: "Wien Hauptbahnhof ↔ Götzendorf/ Wien Hauptbahnhof Gramatneusiedl"

The downstream ``_format_route_title`` rebuild masked the breakage in
the live feed (it parses routes from the description, not the cleaned
title), but any caller that uses ``_clean_title_keep_places`` as a
stand-alone pre-processor saw the corruption.

The fix:

- Join all parts with `` ↔ `` so chained route titles round-trip.
- Use `` / `` (with whitespace on both sides) when re-joining a
  composite endpoint after the slash-split — the previous ``"/ "``
  produced ``B/ C`` which looked like a typo.
"""

from __future__ import annotations

from src.providers.oebb import _clean_title_keep_places


class TestMultiRouteArrowsPreserved:
    def test_two_route_chain_keeps_both_arrows(self) -> None:
        raw = (
            "Wien Hauptbahnhof ↔ Götzendorf / "
            "Wien Hauptbahnhof ↔ Gramatneusiedl"
        )
        out = _clean_title_keep_places(raw)
        assert out.count("↔") == 2
        assert "Götzendorf" in out
        assert "Gramatneusiedl" in out

    def test_two_routes_with_pendler_endpoint(self) -> None:
        raw = (
            "Wien Mitte-Landstraße ↔ Flughafen Wien / "
            "Wien Mitte-Landstraße ↔ Wien Floridsdorf"
        )
        out = _clean_title_keep_places(raw)
        assert out.count("↔") == 2
        assert "Flughafen Wien" in out
        assert "Wien Floridsdorf" in out

    def test_two_routes_with_line_prefix(self) -> None:
        raw = (
            "S 50: Wien Westbahnhof ↔ Wien Hütteldorf / "
            "Wien Hütteldorf ↔ Tullnerbach-Pressbaum"
        )
        out = _clean_title_keep_places(raw)
        assert out.startswith("S 50:")
        assert out.count("↔") == 2

    def test_three_routes_all_arrows_kept(self) -> None:
        raw = "A ↔ B / C ↔ D / E ↔ F"
        out = _clean_title_keep_places(raw)
        assert out.count("↔") == 3


class TestSingleRouteUnchanged:
    def test_simple_route(self) -> None:
        assert _clean_title_keep_places(
            "Wien Hauptbahnhof ↔ Mödling"
        ) == "Wien Hauptbahnhof ↔ Mödling"

    def test_line_prefix_route(self) -> None:
        out = _clean_title_keep_places(
            "S40: Wien Franz-Josefs-Bahnhof ↔ Wien Heiligenstadt"
        )
        assert out == "S40: Wien Franz-Josefs-Bahnhof ↔ Wien Heiligenstadt"


class TestSlashSpacing:
    def test_composite_endpoint_uses_proper_spacing(self) -> None:
        # A composite endpoint like ``Wien/ Flughafen Wien`` (with the
        # slash-then-space form ÖBB sometimes uses) must round-trip
        # with the proper `` / `` separator.
        raw = "Bauarbeiten: Wien/ Flughafen Wien"
        out = _clean_title_keep_places(raw)
        # Must NOT contain the awkward ``X/ Y`` (no leading space).
        assert "/ " not in out or " / " in out
