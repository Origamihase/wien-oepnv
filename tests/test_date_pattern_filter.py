"""Regression tests for Bug W (date/time fragments mis-extracted as route
endpoints).

Real ÖBB descriptions sometimes carry sentences like::

    Wegen Bauarbeiten können von 03.10.2026 (23:15 Uhr) bis
    05.10.2026 (04:40 Uhr) keine Züge fahren.

The new ``_VON_NACH_PLAIN_RE`` pattern would otherwise match the
``von DATUM bis DATUM`` clause and produce a "route" candidate with
two date strings as endpoints. The unknown-unknown classification kept
the message classification right today, but a future change could
expose the misclassification.

The fix tightens ``_looks_like_station_name`` to reject:

- strings starting with a digit (date/time fragment),
- strings with no run of 3+ alphabetic characters (residue of token
  cleanup).
"""

from __future__ import annotations

from src.providers.oebb import _extract_routes, _is_relevant, _looks_like_station_name


class TestDatePatternRejection:
    def test_pure_date_range_yields_no_route(self) -> None:
        # "von 03.10.2026 bis 05.10.2026" is just a date range, not a route.
        routes = _extract_routes(
            "Bauarbeiten",
            "Wegen Bauarbeiten von 03.10.2026 bis 05.10.2026 keine Züge.",
        )
        assert routes == []

    def test_time_range_yields_no_route(self) -> None:
        routes = _extract_routes(
            "Bauarbeiten",
            "Verspätungen von 14:00 bis 18:00.",
        )
        assert routes == []

    def test_real_route_still_works_around_date_range(self) -> None:
        # Real Wien-Pendler description with a date range elsewhere.
        title = "Wien Hbf ↔ Gramatneusiedl"
        desc = (
            "Wegen Bauarbeiten können zwischen Wien Hbf (U) und "
            "Gramatneusiedl Bahnhof von 03.10.2026 (23:15 Uhr) bis "
            "05.10.2026 (04:40 Uhr) keine Züge fahren."
        )
        routes = _extract_routes(title, desc)
        # Only the real route is extracted, not the date range.
        assert routes == [("Wien", "Gramatneusiedl")]
        assert _is_relevant(title, desc) is True


class TestLooksLikeStationName:
    def test_real_station_names(self) -> None:
        assert _looks_like_station_name("Mödling")
        assert _looks_like_station_name("Wien Hbf")
        assert _looks_like_station_name("St. Pölten")
        assert _looks_like_station_name("Wien 10.: Favoriten")
        assert _looks_like_station_name("Wien Mitte-Landstraße")

    def test_date_and_time_fragments_rejected(self) -> None:
        assert not _looks_like_station_name("03.10.2026")
        assert not _looks_like_station_name("03 .10.2026 (23:15 Uhr)")
        assert not _looks_like_station_name("14:00")
        assert not _looks_like_station_name("13.04.2026")
        assert not _looks_like_station_name("23:15")
        # "23:15 Uhr" starts with a digit → reject
        assert not _looks_like_station_name("23:15 Uhr")

    def test_too_short_alpha_rejected(self) -> None:
        # "Hb (U)" has only "Hb" + "U" — no 3+ alpha run.
        assert not _looks_like_station_name("Hb (U)")
        assert not _looks_like_station_name("U")
        assert not _looks_like_station_name("a b")

    def test_empty_rejected(self) -> None:
        assert not _looks_like_station_name("")
        assert not _looks_like_station_name("   ")
