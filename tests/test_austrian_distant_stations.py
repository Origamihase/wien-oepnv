"""Regression tests for the manually added Austrian and foreign distant
stations.

The directory used to carry only ``München Hbf`` and ``Roma Termini`` as
``in_vienna=False, pendler=False`` entries. Wien↔Distant routes through
classical Austrian Hauptbahnhöfe (Graz, Linz, Salzburg, …) therefore
classified as Wien↔Unknown — the strict route check could only reject
them after the title-residual heuristic kicked in, and the
single-station fall-through occasionally let them through when the
distant station was missing entirely.

This file locks in the new directory state and verifies that the
classification path now correctly flags real cache items.
"""

from __future__ import annotations

import pytest

from src.providers.oebb import _is_relevant
from src.utils.stations import station_info


AUSTRIAN_DISTANT = [
    "Graz Hbf",
    "Linz Hbf",
    "Salzburg Hbf",
    "Innsbruck Hbf",
    "Klagenfurt Hbf",
    "Villach Hbf",
    "Wels Hbf",
    "Bregenz",
    "Bruck an der Mur",
    "Vöcklabruck",
    "Mürzzuschlag",
    "Leoben Hbf",
    "Wörgl Hbf",
    "Bischofshofen",
]

FOREIGN_DISTANT = [
    "Passau Hbf",
    "Praha hl.n.",
    "Budapest-Keleti",
    "Bratislava hl.st.",
    "Berlin Hbf",
    "Venezia Santa Lucia",
    "Ljubljana",
    "Zagreb Glavni kolodvor",
]


@pytest.mark.parametrize("name", AUSTRIAN_DISTANT + FOREIGN_DISTANT)
def test_distant_stations_classified(name: str) -> None:
    info = station_info(name)
    assert info is not None, f"{name} must be in stations.json"
    assert info.in_vienna is False
    assert info.pendler is False


@pytest.mark.parametrize(
    "alias,canonical",
    [
        ("Graz", "Graz Hbf"),
        ("Linz", "Linz Hbf"),
        ("Salzburg", "Salzburg Hbf"),
        ("Innsbruck", "Innsbruck Hbf"),
        ("Klagenfurt", "Klagenfurt Hbf"),
        ("Villach", "Villach Hbf"),
        ("Wels", "Wels Hbf"),
        ("Bruck/Mur", "Bruck an der Mur"),
        ("Passau", "Passau Hbf"),
        ("Praha", "Praha hl.n."),
        ("Budapest", "Budapest-Keleti"),
        ("Bratislava", "Bratislava hl.st."),
        ("Berlin", "Berlin Hbf"),
        ("Venezia", "Venezia Santa Lucia"),
        ("Zagreb", "Zagreb Glavni kolodvor"),
    ],
)
def test_distant_aliases_resolve(alias: str, canonical: str) -> None:
    info = station_info(alias)
    assert info is not None, f"{alias} should resolve via alias"
    assert info.name == canonical


class TestRouteRelevanceWithNewDistant:
    """Routes through the newly registered distant stations must now
    classify reliably (Wien↔Distant → drop) without relying on the
    title-residual heuristic or sentence-boundary edge cases.
    """

    def test_wien_graz_route_dropped(self) -> None:
        assert (
            _is_relevant(
                "Bauarbeiten",
                "Strecke ab Wien Hbf bis Graz Hbf gesperrt.",
            )
            is False
        )

    def test_wien_linz_route_dropped(self) -> None:
        assert (
            _is_relevant(
                "Bauarbeiten: Wien Hauptbahnhof ↔ Linz Hbf",
                "Wegen Bauarbeiten zwischen Wien Hbf und Linz Hbf.",
            )
            is False
        )

    def test_wien_salzburg_route_dropped(self) -> None:
        assert (
            _is_relevant(
                "Bauarbeiten",
                "Wegen Bauarbeiten zwischen Wien Hbf und Salzburg Hbf.",
            )
            is False
        )

    def test_distant_distant_route_dropped(self) -> None:
        # Real cache item: Bruck/Mur ↔ Graz
        assert (
            _is_relevant(
                "Bauarbeiten: Bruck/Mur Graz",
                "von Bruck/Mur Bahnhof nach Graz Hbf.",
            )
            is False
        )

    def test_voecklabruck_salzburg_dropped(self) -> None:
        # Real cache item: Vöcklabruck/Salzburg
        assert (
            _is_relevant(
                "Bauarbeiten: Vöcklabruck/Salzburg",
                "Wegen Bauarbeiten in Vöcklabruck Bahnhof werden Fernverkehrszüge umgeleitet.",
            )
            is False
        )

    def test_passau_wien_still_dropped(self) -> None:
        # Sanity: the original user-reported leak.
        assert (
            _is_relevant(
                "DB-Bauarbeiten: Passau Wien Hauptbahnhof",
                "fährt zwischen Passau Hbf und Wien Hbf in der Nacht.",
            )
            is False
        )
