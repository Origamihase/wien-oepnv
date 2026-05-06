"""Tests for the user-facing ``display_name`` station-name override.

The ÖBB station directory uses the official name ``Wien Mitte-Landstraße``,
but the colloquial label ``Wien Mitte`` reads cleaner in the 2-line TV feed.
``display_name`` keeps the canonical directory entry authoritative while
overriding the rendered name at feed-build time.
"""

from __future__ import annotations

from src.providers.oebb import _format_route_title
from src.utils.stations import canonical_name, display_name, station_info


class TestDisplayNameOverride:
    def test_wien_mitte_landstrasse_renders_as_wien_mitte(self) -> None:
        assert display_name("Wien Mitte-Landstraße") == "Wien Mitte"

    def test_alias_input_is_not_lookup_aware(self) -> None:
        # display_name does not run alias resolution; it operates on names
        # already in canonical form (or close to it).
        assert display_name("Wien Mitte") == "Wien Mitte"

    def test_strips_trailing_vor_suffix(self) -> None:
        assert display_name("Wien Mitte-Landstraße (VOR)") == "Wien Mitte"

    def test_unmapped_names_pass_through_unchanged(self) -> None:
        assert display_name("Flughafen Wien") == "Flughafen Wien"
        assert display_name("Wien Hauptbahnhof") == "Wien Hauptbahnhof"

    def test_strips_vor_suffix_for_unmapped_names(self) -> None:
        assert display_name("Wien Hauptbahnhof (VOR)") == "Wien Hauptbahnhof"

    def test_handles_empty_input(self) -> None:
        assert display_name("") == ""
        assert display_name(None) == ""


class TestCanonicalNameUnchanged:
    """The directory entry remains authoritative — only the rendered label
    flips. canonical_name still returns the official ÖBB name so that
    cross-provider lookups, alias matching and de-duplication keep working.
    """

    def test_canonical_name_still_returns_official_name(self) -> None:
        assert canonical_name("Wien Mitte") == "Wien Mitte-Landstraße"
        assert canonical_name("Wien Mitte-Landstraße") == "Wien Mitte-Landstraße"

    def test_station_info_name_still_official(self) -> None:
        info = station_info("Wien Mitte-Landstraße")
        assert info is not None
        assert info.name == "Wien Mitte-Landstraße"


class TestRouteTitleUsesDisplayName:
    def test_format_route_title_renames_wien_mitte(self) -> None:
        title = _format_route_title([("Flughafen Wien", "Wien Mitte-Landstraße")])
        assert title == "Wien Mitte ↔ Flughafen Wien"

    def test_format_route_title_dedupes_after_rename(self) -> None:
        # Both spellings collapse to "Wien Mitte" — the second route must
        # not appear twice in the formatted title.
        routes = [
            ("Flughafen Wien", "Wien Mitte-Landstraße"),
            ("Flughafen Wien", "Wien Mitte"),
        ]
        title = _format_route_title(routes)
        assert title == "Wien Mitte ↔ Flughafen Wien"
