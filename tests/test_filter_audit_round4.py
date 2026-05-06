"""Audit-Round-4 regression tests.

Bug L: a Wien station mentioned alongside a known-distant station in the
single-station fall-through used to KEEP the message even though the
described route is almost certainly Wien↔Distant. The user reported

    Bauarbeiten: Wien/München Roma Termini
    Wegen Bauarbeiten werden … die NJ-Züge … via Salzburg/Kufstein … umgeleitet

as an obvious leak: the actual disrupted route is Wien↔Rom, not a
Wien-internal or Wien↔Pendler trip.

The fix: in the single-station path, drop any message that mentions a
known-distant station (``in_vienna=False`` and ``pendler=False`` in
stations.json) — even if a Wien station is co-mentioned. Standalone Wien
disruptions don't drag in München / Roma names; route-style messages
do.

Two further audit findings are *data* issues that this PR cannot fix in
code:

- **Bug M**: many real Wien commuter stations (Felixdorf, Traiskirchen
  Aspangbahn, Gramatneusiedl, Götzendorf, Bruck/Leitha, Semmering,
  Payerbach-Reichenau, Krems, Eisenstadt, Pamhagen, …) are absent from
  ``data/stations.json``. Wien↔<missing-Pendler> routes are therefore
  misclassified as Wien↔Unknown and dropped — a false negative the
  filter cannot recover from without directory updates.

- **Bug N**: when a title carries multiple proper-noun-shaped tokens but
  only one resolves (because the others are missing from the directory,
  see bug M), the single-station path keeps the message. Example:
  ``Bauarbeiten: Wiener Neustadt Hauptbahnhof Semmering`` with no
  zwischen pattern in the description — a Pendler↔Distant Fernverkehr
  route stays in the feed because Semmering is unknown to the directory.
  Resolving this generally requires more data (Bug M) or a stricter
  text-based heuristic that risks false positives for legitimate
  Pendler-only messages.
"""

from __future__ import annotations

from src.providers.oebb import _is_relevant


class TestSingleStationDropsOnDistant:
    """Bug L: a known-distant mention next to a Wien mention drops."""

    def test_wien_muenchen_rome_dropped(self) -> None:
        # Exact failing payload from the live cache.
        title = "Bauarbeiten: Wien/München Roma Termini"
        desc = (
            "Wegen Bauarbeiten werden von 22.08.2026 bis 20.09.2026 die "
            "NJ-Züge 40233 und 40294 über Salzburg/Kufstein und die "
            "NJ-Züge 294 und 295 über Brennero/Brenner umgeleitet."
        )
        assert _is_relevant(title, desc) is False

    def test_wien_only_facility_notice_dropped(self) -> None:
        # Per project spec ("Defekte Aufzüge ... haben im Feed nichts
        # zu suchen") a facility-only notice must drop even when it
        # mentions a Wien station.
        title = "Aufzug defekt: Wien Hauptbahnhof"
        desc = "Aufzug am Bahnsteig 12 außer Betrieb."
        assert _is_relevant(title, desc) is False

    def test_wien_only_real_disruption_kept(self) -> None:
        # A real transit disruption at a single Wien station still keeps.
        title = "Bauarbeiten Wien Hauptbahnhof"
        desc = "Wegen Bauarbeiten kein Verkehr."
        assert _is_relevant(title, desc) is True

    def test_wien_pendler_mention_kept(self) -> None:
        # Sanity: Wien + Pendler mentions (no distant) stay relevant.
        title = "Verspätungen Wien Hauptbahnhof Mödling"
        desc = "Wegen Sturm Verspätungen im Raum Wien und Mödling."
        assert _is_relevant(title, desc) is True

    def test_pendler_with_distant_dropped(self) -> None:
        # Pendler + Distant mentions imply Pendler↔Distant route → drop.
        title = "Sturm Mödling München"
        desc = "Wegen Sturm einige Verspätungen."
        assert _is_relevant(title, desc) is False

    def test_distant_only_dropped(self) -> None:
        # Sanity: a distant-only message stays rejected.
        title = "Bauarbeiten München Hbf"
        desc = "Im Raum München Verspätungen."
        assert _is_relevant(title, desc) is False


class TestImplicitRouteToUnknown:
    """Bug N: titles like ``Wiener Neustadt Hauptbahnhof Semmering`` (Pendler
    + unknown second endpoint, no zwischen-pattern in description) used to
    slip through the single-station path. The title-residual heuristic now
    treats a capitalized non-stop-word token *after* a known station as an
    implicit second endpoint and drops the message.
    """

    def test_pendler_unknown_route_dropped(self) -> None:
        # User-reported case: Wiener Neustadt is Pendler, Semmering is
        # absent from the directory but clearly the second endpoint of a
        # Fernverkehr route.
        title = "Bauarbeiten: Wiener Neustadt Hauptbahnhof Semmering"
        desc = (
            "Wegen Bauarbeiten werden von 07.09.2026 bis 12.12.2026 "
            "(04:30 Uhr) einige Fernverkehrszüge umgeleitet."
        )
        assert _is_relevant(title, desc) is False

    def test_wien_unknown_route_dropped(self) -> None:
        # Same shape, with Wien instead of a Pendler station.
        assert _is_relevant("Bauarbeiten: Wien Hauptbahnhof Brixlegg", "x") is False

    def test_wien_only_kept(self) -> None:
        # A real transit disruption at a single Wien station must keep.
        assert _is_relevant("Bauarbeiten: Wien Hauptbahnhof", "Wegen Bauarbeiten gesperrt.") is True

    def test_wien_pendler_pair_kept(self) -> None:
        # Pendler is a known station — the residual check must not fire.
        assert (
            _is_relevant("Bauarbeiten: Wien Hauptbahnhof Mödling", "x") is True
        )

    def test_explicit_arrow_route_kept(self) -> None:
        # ↔-titles are handled by _extract_routes, not by this heuristic.
        assert _is_relevant("Wien Hauptbahnhof ↔ Mödling", "") is True

    def test_token_before_wien_does_not_imply_route(self) -> None:
        # Position-Constraint: tokens BEFORE the last known station are
        # sentence preamble, not implicit second endpoints. Use a
        # transit-like preamble word so the new facility/weather filter
        # doesn't intercept the message before this heuristic runs.
        assert (
            _is_relevant(
                "Bauarbeiten im Bereich Wien Hauptbahnhof",
                "Verzögerungen bei der S-Bahn Wien.",
            )
            is True
        )

    def test_time_word_after_wien_kept(self) -> None:
        # "Wochenende" is a German common noun in our stop list.
        assert (
            _is_relevant(
                "Bauarbeiten Wien Hauptbahnhof am Wochenende", "Hinweis"
            )
            is True
        )
