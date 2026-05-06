"""Regression tests for Bug Z1 (route endpoint over-extends into the
"einige/keine Nahverkehrszüge" clause that follows the destination).

Real ÖBB descriptions in the cache contain sentences like::

    Wegen Bauarbeiten zwischen Wien Hbf und Bruck/Leitha Bahnhof einige
    Nahverkehrszüge ausgefallen.

Before the fix, ``_ZWISCHEN_PLAIN_RE``'s lookahead did not list the
quantifiers ``einige|keine|alle|mehrere|wenige|sämtliche`` as boundaries,
so the non-greedy ``b`` capture absorbed the entire affected-train
clause and produced frankenstring endpoints like
``"Bruck/Leitha Bahnhof einige Nahverkehrszüge"``. The strict route
classifier then failed to resolve the second endpoint and the route
silently became "unknown" — masking real Wien↔Pendler messages and
producing garbled feed titles when ``_format_route_title`` was called.

The fix adds ``einige|keine|kein|alle|mehrere|wenige|sämtliche`` to the
lookahead's word-boundary alternation so the endpoint stops cleanly at
the start of the quantifier-led noun phrase.
"""

from __future__ import annotations

from src.providers.oebb import _extract_routes


class TestEndpointOverExtension:
    def test_einige_nahverkehrszuege_does_not_absorb_endpoint(self) -> None:
        routes = _extract_routes(
            "Bauarbeiten",
            "Wegen Bauarbeiten zwischen Wien Hbf und Bruck/Leitha Bahnhof "
            "einige Nahverkehrszüge ausgefallen.",
        )
        assert routes == [("Wien", "Bruck/Leitha")]

    def test_keine_nahverkehrszuege_does_not_absorb_endpoint(self) -> None:
        routes = _extract_routes(
            "Bauarbeiten",
            "Zwischen Wien Westbahnhof und Wien Hütteldorf keine "
            "Nahverkehrszüge.",
        )
        assert routes == [("Wien Westbahnhof", "Wien Hütteldorf")]

    def test_alle_zuege_does_not_absorb_endpoint(self) -> None:
        routes = _extract_routes(
            "Störung",
            "Aufgrund einer Weichenstörung sind zwischen Wien Hbf und "
            "Mödling alle Züge betroffen.",
        )
        assert routes == [("Wien", "Mödling")]

    def test_mehrere_zuege_does_not_absorb_endpoint(self) -> None:
        routes = _extract_routes(
            "Verspätung",
            "Zwischen Wien Meidling und Baden mehrere Züge mit "
            "Verspätung.",
        )
        assert routes == [("Wien Meidling", "Baden")]

    def test_kein_singular_handled(self) -> None:
        # "kein Zug" (singular) — same logic as "keine Züge".
        routes = _extract_routes(
            "Bauarbeiten",
            "Wegen Gleisbauarbeiten zwischen Wien Hbf und Mödling kein "
            "Zug verfügbar.",
        )
        assert routes == [("Wien", "Mödling")]

    def test_saemtliche_handled(self) -> None:
        # "sämtliche Verbindungen" — formal variant.
        routes = _extract_routes(
            "Sperre",
            "Zwischen Wien Hbf und Wiener Neustadt sämtliche Verbindungen "
            "ausgefallen.",
        )
        assert routes == [("Wien", "Wiener Neustadt")]
