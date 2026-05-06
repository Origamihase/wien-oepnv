"""Audit-round-6 regression: sentence-boundary handling in description.

Bug R surfaced when a description carried multiple sentences after the
``zwischen X und Y`` route phrase:

    Bauarbeiten zwischen Wien Hbf und Mödling. Auch Auswirkung auf
    Reisende aus München.

The non-greedy regex used to extend the ``b`` capture across the full
sentence boundary, ending with
``b="Mödling. Auch Auswirkung auf Reisende aus München"``. After the
post-hoc Bahnhof normalisation that endpoint failed to resolve and the
strict route check rejected an otherwise-valid Wien↔Pendler message.

Two fixes work together:

- ``_normalize_endpoint_name`` now truncates an endpoint at the first
  ``". "`` when the part before the period resolves against the
  directory. Abbreviations like "St. Pölten" stay intact because "St"
  alone doesn't resolve.
- ``_ZWISCHEN_PLAIN_RE`` accepts a sentence-starter list (``Auch``,
  ``Bitte``, ``Wir``, ``Es``, …) as a period-terminated boundary so the
  regex stops at the real sentence end and ``finditer`` continues into
  the next ``zwischen`` clause.
"""

from __future__ import annotations

from src.providers.oebb import _extract_routes, _is_relevant


class TestSentenceBoundaryInDescription:
    def test_period_followed_by_auch_terminates_endpoint(self) -> None:
        routes = _extract_routes(
            "Bauarbeiten Wien Hbf",
            "Bauarbeiten zwischen Wien Hbf und Mödling. Auch Auswirkung auf "
            "Reisende aus München.",
        )
        assert routes == [("Wien", "Mödling")]

    def test_period_followed_by_wir_bitten_terminates(self) -> None:
        routes = _extract_routes(
            "Bauarbeiten",
            "zwischen Wien Hbf und Mödling. Wir bitten um Verständnis.",
        )
        assert routes == [("Wien", "Mödling")]

    def test_period_followed_by_bitte_terminates(self) -> None:
        routes = _extract_routes(
            "Bauarbeiten",
            "zwischen Wien Hbf und Mödling. Bitte beachten Sie unsere Hinweise.",
        )
        assert routes == [("Wien", "Mödling")]

    def test_st_poelten_abbreviation_stays_intact(self) -> None:
        # Defence in depth: the "St." abbreviation period must not be
        # treated as a sentence end.
        routes = _extract_routes(
            "Bauarbeiten",
            "zwischen Wien Hbf und St. Pölten ist der Verkehr eingestellt.",
        )
        assert routes == [("Wien", "St. Pölten")]

    def test_two_sentences_two_routes_extracted(self) -> None:
        # Two zwischen clauses in two sentences — both must surface.
        routes = _extract_routes(
            "Bauarbeiten",
            "Wegen Bauarbeiten zwischen Wien Hbf und München. Auch zwischen "
            "Wien Hbf und Mödling.",
        )
        assert routes == [("Wien", "München"), ("Wien", "Mödling")]

    def test_two_sentences_keeps_overall_message(self) -> None:
        # Even if the first route is Wien↔Distant (München), the second
        # route Wien↔Pendler (Mödling) should keep the message.
        assert (
            _is_relevant(
                "Bauarbeiten",
                "Wegen Bauarbeiten zwischen Wien Hbf und München. Auch "
                "zwischen Wien Hbf und Mödling.",
            )
            is True
        )

    def test_single_route_followed_by_collateral_distant_kept(self) -> None:
        # Single Wien↔Pendler route extracted, München is just collateral
        # mention in the next sentence — message must keep.
        assert (
            _is_relevant(
                "Bauarbeiten Wien Hbf",
                "Bauarbeiten zwischen Wien Hbf und Mödling. Auch Auswirkung "
                "auf Reisende aus München.",
            )
            is True
        )
