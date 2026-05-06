"""Regression tests for the user-reported feed examples.

These cases reflect the strict route filter spec:

- Wien ↔ Wien → keep
- Wien ↔ Pendler → keep
- Pendler ↔ Pendler → drop
- Wien ↔ Distant/Unknown → drop
- Distant ↔ Distant → drop

The titles are also checked: route messages must be rendered as a clean
``A ↔ B`` form with canonical (and expanded) station names, without category
prefixes such as "Bauarbeiten:" or "DB-Bauarbeiten:".
"""

from __future__ import annotations

from src.providers.oebb import (
    _clean_title_keep_places,
    _extract_line_prefix,
    _extract_routes,
    _format_route_title,
    _is_relevant,
    _route_is_wien_relevant,
)


def _build_title(raw_title: str, description: str) -> str:
    """Reproduce the title pipeline used inside ``fetch_events``."""
    cleaned = _clean_title_keep_places(raw_title)
    line_prefix, _ = _extract_line_prefix(cleaned)
    routes = _extract_routes(cleaned, description)
    relevant_routes = [(a, b) for (a, b) in routes if _route_is_wien_relevant(a, b)]
    if relevant_routes:
        return _format_route_title(relevant_routes, line_prefix)
    return cleaned


class TestUserReportedDropExamples:
    """Examples that the user reported as wrongly KEPT in the feed."""

    def test_passau_wien_hauptbahnhof_dropped(self) -> None:
        # User example #1: DB long-distance disruption Passau ↔ Wien Hbf.
        # Passau is not a Wiener Bahnhof and not in the Pendler list, so
        # this Wien ↔ Distant connection must be dropped from the feed.
        title = "DB-Bauarbeiten: geänderte Fahrzeiten: Passau Wien Hauptbahnhof"
        desc = (
            "Wegen Bauarbeiten der Deutschen Bahn (DB) fährt<br>"
            "zwischen <b>Passau Hbf</b> und <b>Wien Hbf<br>in der Nacht</b> "
            "von <b>18.05.</b> auf <b>19.05.2026<br></b>der Zug NJ 491 nicht"
        )
        cleaned = _clean_title_keep_places(title)
        assert _is_relevant(cleaned, desc) is False

    def test_pendler_pendler_route_dropped(self) -> None:
        # Flughafen Wien (Pendler) ↔ Wolfsthal (Pendler) — neither is in
        # Vienna, so the connection must be dropped per spec.
        title = "Bauarbeiten: Flughafen Wien Wolfsthal"
        desc = (
            "Wegen Bauarbeiten können<br>zwischen <b>Flughafen Wien Bahnhof</b>"
            " und <b>Wolfsthal Bahnhof</b><br>am 17.09.2026"
        )
        cleaned = _clean_title_keep_places(title)
        assert _is_relevant(cleaned, desc) is False

    def test_rex7_pendler_pendler_segment_dropped(self) -> None:
        # The full line REX 7 stops at several Wiener Bahnhöfe but the
        # actually disrupted segment is Flughafen Wien ↔ Wolfsthal — both
        # Pendler, no Wien.
        title = "REX 7: Bauarbeiten: Wien Floridsdorf/Flughafen Wien Wolfsthal"
        desc = (
            "Wegen Bauarbeiten können <br>"
            "zwischen <b>Flughafen Wien Bahnhof</b> und <b>Wolfsthal Bahnhof </b><br>"
            "am 19.02.2026 ..."
        )
        cleaned = _clean_title_keep_places(title)
        assert _is_relevant(cleaned, desc) is False


class TestUserReportedKeepExamples:
    """Examples the user wants kept, with cleaner titles."""

    def test_flughafen_wien_mitte_kept_with_clean_title(self) -> None:
        title = "Bauarbeiten: Flughafen Wien Wien Mitte-Landstraße"
        desc = (
            "Wegen Bauarbeiten können<br>"
            "von <b>13.04.2026</b> bis <b>28.05.2026</b><br>"
            "zwischen <b>Flughafen Wien Bahnhof</b> und <b>Wien Mitte-Landstraße Bahnhof</b>"
        )
        cleaned = _clean_title_keep_places(title)
        assert _is_relevant(cleaned, desc) is True
        # Vienna endpoint comes first; Pendler endpoint after the arrow.
        # The canonical "Wien Mitte-Landstraße" is rendered as "Wien Mitte"
        # in the user-facing feed (display override).
        assert _build_title(title, desc) == "Wien Mitte ↔ Flughafen Wien"

    def test_wien_westbf_huetteldorf_kept_with_expanded_title(self) -> None:
        title = "Bauarbeiten: Wien Westbf Wien Hütteldorf"
        desc = (
            "Wegen Bauarbeiten können<br>"
            "zwischen <b>Wien Westbahnhof (U)</b> und <b>Wien Hütteldorf Bahnhof (U)</b><br>"
            "von <b>03.06.2026</b>"
        )
        cleaned = _clean_title_keep_places(title)
        assert _is_relevant(cleaned, desc) is True
        # 'Westbf' is expanded to 'Westbahnhof' for readability.
        assert _build_title(title, desc) == "Wien Westbahnhof ↔ Wien Hütteldorf"

    def test_wien_hbf_flughafen_wien_kept_with_clean_title(self) -> None:
        title = "Bauarbeiten: Wien Hauptbahnhof Flughafen Wien"
        desc = (
            "Wegen Bauarbeiten können zwischen <b>Wien Hbf (U)</b>"
            " und <b>Flughafen Wien Bahnhof</b> von <b>10.07.2026</b>"
        )
        cleaned = _clean_title_keep_places(title)
        assert _is_relevant(cleaned, desc) is True
        assert _build_title(title, desc) == "Wien Hauptbahnhof ↔ Flughafen Wien"

    def test_s50_line_prefix_preserved_in_title(self) -> None:
        title = "S 50: Bauarbeiten: Wien Westbf Wien Hütteldorf/Tullnerbach-Pressbaum"
        desc = (
            "Wegen Bauarbeiten können<br>"
            "von <b>03.06.2026</b> (23:00 Uhr) bis <b>08.06.2026</b><br>"
            "zwischen <b>Wien Westbahnhof (U) </b>und <b>Wien Hütteldorf Bahnhof (U)</b>"
        )
        cleaned = _clean_title_keep_places(title)
        assert _is_relevant(cleaned, desc) is True
        # Line prefix 'S 50' must survive the rebuild.
        assert _build_title(title, desc) == "S 50: Wien Westbahnhof ↔ Wien Hütteldorf"

    def test_multi_route_message_kept(self) -> None:
        title = "Bauarbeiten: Wien Praterstern Wien Meidling/ Wien Hauptbahnhof Wien Hütteldorf"
        desc = (
            "Wegen Bauarbeiten können<br>"
            "zwischen <b>Wien Praterstern Bahnhof (U)</b> und <b>Wien Meidling Bahnhof (U)</b>, und<br>"
            "zwischen <b>Wien Hbf (U)</b> und <b>Wien Hütteldorf Bahnhof (U)</b>"
        )
        cleaned = _clean_title_keep_places(title)
        assert _is_relevant(cleaned, desc) is True
        rebuilt = _build_title(title, desc)
        # Both routes are surfaced separated by ' / '.
        assert "Wien Praterstern ↔ Wien Meidling" in rebuilt
        assert "Wien Hauptbahnhof ↔ Wien Hütteldorf" in rebuilt


class TestStrictRouteSpec:
    """Narrow unit tests for the strict route classification rule."""

    def test_vienna_to_vienna_relevant(self) -> None:
        assert _route_is_wien_relevant("Wien Westbahnhof", "Wien Hütteldorf") is True

    def test_vienna_to_pendler_relevant(self) -> None:
        assert _route_is_wien_relevant("Wien Hauptbahnhof", "Mödling") is True
        assert _route_is_wien_relevant("Mödling", "Wien Hauptbahnhof") is True

    def test_pendler_to_pendler_dropped(self) -> None:
        assert _route_is_wien_relevant("Mödling", "Baden") is False

    def test_vienna_to_distant_dropped(self) -> None:
        # München is in stations.json with in_vienna=False, pendler=False.
        assert _route_is_wien_relevant("Wien Hauptbahnhof", "München Hbf") is False

    def test_vienna_to_unknown_dropped(self) -> None:
        # Passau is not in stations.json — treated as unknown/distant.
        assert _route_is_wien_relevant("Wien Hauptbahnhof", "Passau Hbf") is False

    def test_unknown_to_unknown_dropped(self) -> None:
        assert _route_is_wien_relevant("Bratislava", "Budapest") is False
