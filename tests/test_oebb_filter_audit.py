"""Regression tests for filter bugs found during the post-merge audit.

Each section describes the exact failure mode that the fix addresses so a
future regression has a chance to show up under the right test name.
"""

from __future__ import annotations

from src.providers.oebb import (
    _extract_routes,
    _find_stations_in_text,
    _is_relevant,
)
from src.feed.merge import _parse_title, deduplicate_fuzzy


class TestStPoeltenPeriodTruncation:
    """Bug B: a bare period in the lookahead truncated abbreviated names.

    "und St. Pölten ist …" used to capture endpoint "St" because the
    boundary class included ".". The fix accepts a period as a boundary
    only at the end of the description.
    """

    def test_st_poelten_kept_intact_before_ist(self) -> None:
        routes = _extract_routes(
            "Bauarbeiten",
            "zwischen Wien Hbf und St. Pölten ist der Verkehr eingestellt.",
        )
        assert routes == [("Wien", "St. Pölten")]
        assert _is_relevant(
            "Bauarbeiten",
            "zwischen Wien Hbf und St. Pölten ist der Verkehr eingestellt.",
        ) is True

    def test_st_poelten_kept_intact_before_gesperrt(self) -> None:
        routes = _extract_routes(
            "Bauarbeiten",
            "Strecke zwischen Wien Hbf und St. Pölten Hbf gesperrt",
        )
        assert routes
        assert routes[0][0] == "Wien"
        assert routes[0][1].startswith("St. Pölten")


class TestRouteBoundaryWords:
    """Bug C: missing boundary words in the lookahead let the regex
    extend the second endpoint into surrounding prose."""

    def test_aufgrund_terminates_route(self) -> None:
        routes = _extract_routes(
            "Bauarbeiten",
            "zwischen Wien Hbf und Mödling aufgrund Sturm",
        )
        assert routes == [("Wien", "Mödling")]

    def test_wegen_terminates_route(self) -> None:
        routes = _extract_routes(
            "Bauarbeiten",
            "zwischen Wien Hbf und Mödling wegen Bauarbeiten",
        )
        assert routes == [("Wien", "Mödling")]

    def test_em_dash_terminates_route(self) -> None:
        routes = _extract_routes(
            "Bauarbeiten",
            "zwischen Wien und Mödling — bitte umsteigen",
        )
        assert routes == [("Wien", "Mödling")]

    def test_period_at_end_terminates_route(self) -> None:
        routes = _extract_routes("Bauarbeiten", "zwischen Wien Hbf und Mödling.")
        assert routes == [("Wien", "Mödling")]


class TestFakeRouteFilter:
    """Bug D: a regex match between two non-station phrases must not block
    the single-station fall-through path."""

    def test_aufzug_zwischen_bahnsteig_falls_through_to_station_match(self) -> None:
        # The "zwischen Bahnsteig 1 und Bahnsteig 5" reads like a route
        # but actually describes a facility-internal segment. Both
        # endpoints fail to resolve, so the route is discarded and
        # the single-station path picks up "Wien Mitte" → relevant.
        title = "Aufzug Wien Mitte"
        desc = "Aufzug zwischen Bahnsteig 1 und Bahnsteig 5 in Wien Mitte defekt"
        assert _extract_routes(title, desc) == []
        assert _is_relevant(title, desc) is True

    def test_unknown_unknown_route_in_arrow_title_still_drops(self) -> None:
        # "Innsbruck Hbf ↔ Salzburg Hbf" — both endpoints unknown but
        # plausibly station-shaped. The candidate is dropped from the
        # route list, and the single-station path finds nothing in the
        # directory, so the message is correctly rejected.
        assert (
            _is_relevant("Innsbruck Hbf ↔ Salzburg Hbf", "Verspätung.") is False
        )

    def test_lindau_st_margrethen_sg_dropped(self) -> None:
        # Both endpoints unknown. Falls through, but the abbreviation
        # "SG" must NOT alias-match Wien Grillgasse anymore (the
        # ≥3-alpha-char filter in _find_stations_in_text guards this).
        assert (
            _is_relevant(
                "Lindau (Bodensee) Reutin ↔ ST. MARGRETHEN SG",
                "Wegen Bauarbeiten der Deutschen Bahn (DB) können zwischen "
                "Lindau (Bodensee) Reutin Bahnhof und ST. MARGRETHEN SG…",
            )
            is False
        )


class TestNoiseTokenFilter:
    """The arrow character "↔" used to combine with adjacent generic
    aliases ("Hbf ↔") and silently produce "Wien Hauptbahnhof" through
    the station directory's expansion rules."""

    def test_arrow_token_does_not_create_phantom_wien_hauptbahnhof(self) -> None:
        # No real Vienna mention → must return no Wien station.
        found = _find_stations_in_text("Innsbruck Hbf ↔ Salzburg Hbf Verspätung.")
        assert "Wien Hauptbahnhof" not in found

    def test_two_letter_abbreviation_does_not_match_short_alias(self) -> None:
        # "SG" alone used to alias to Wien Grillgasse via the directory.
        found = _find_stations_in_text("ST. MARGRETHEN SG")
        assert all(not name.startswith("Wien") for name in found)


class TestLinePrefixWithSpace:
    """Bug A: cross-provider merge silently failed for ÖBB-style line
    prefixes like "REX 7:" because _LINE_PREFIX_RE didn't allow internal
    whitespace, so the line-overlap check yielded an empty set."""

    def test_rex_7_with_space_parsed_as_single_line(self) -> None:
        lines, name = _parse_title("REX 7: Bauarbeiten")
        assert lines == {"REX7"}
        assert name == "Bauarbeiten"

    def test_rex_7_and_rex7_normalise_to_same_token(self) -> None:
        spaced, _ = _parse_title("REX 7: Bauarbeiten Wien Hbf")
        compact, _ = _parse_title("REX7: Bauarbeiten Wien Hbf")
        assert spaced == compact == {"REX7"}

    def test_s_50_with_space_parsed(self) -> None:
        lines, name = _parse_title("S 50: Wien Westbahnhof")
        assert lines == {"S50"}
        assert name == "Wien Westbahnhof"

    def test_multi_segment_oebb_lines(self) -> None:
        lines, _ = _parse_title("REX 7/REX 8: Bauarbeiten")
        assert lines == {"REX7", "REX8"}

    def test_cross_provider_merge_works_with_space_prefix(self) -> None:
        # Same incident reported by both providers. ÖBB uses "REX 7:",
        # VOR uses "REX7:" — without the fix they would not merge.
        items = [
            {
                "guid": "oebb-1",
                "_identity": "oebb|1",
                "source": "ÖBB",
                "title": "REX 7: Bauarbeiten Wien Hauptbahnhof",
                "description": "Strecke gesperrt",
            },
            {
                "guid": "vor-1",
                "_identity": "vor|1",
                "source": "VOR/VAO",
                "title": "REX7: Bauarbeiten Wien Hauptbahnhof",
                "description": "Echtzeit Strecke",
            },
        ]
        result = deduplicate_fuzzy(items)
        assert len(result) == 1
        # VOR wins as master per provider priority logic.
        assert result[0]["source"] == "VOR/VAO"
