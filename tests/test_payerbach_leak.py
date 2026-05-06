"""Regression tests for the user-reported Payerbach-Mürzzuschlag leak.

The live ÖBB cache contained:

    Bauarbeiten: Mürzzuschlag Payerbach-Reichenau an der Rax
    Bauarbeiten: Payerbach-Reichenau an der Rax Mürzzuschlag

Both describe a Distant ↔ Distant route (neither station is in Vienna or
on the Pendler whitelist) and must NOT appear in the feed.

Two bugs combined to let the first variant slip through:

J. ``canonical_name("vor")`` returned ``Wien Hauptbahnhof`` because several
   ``Hbf (VOR)``-style aliases in stations.json normalize down to a key
   of just ``"vor"``. The German preposition ``vor`` (e.g. "vor
   Reiseantritt") in a description then aliased to a flagship Wien
   station and tripped the single-station fall-through path.

K. The previous Bug-D fix discarded a route candidate whenever both
   endpoints failed station_info lookup. The ``Aufzug zwischen Bahnsteig
   1 und Bahnsteig 5 in Wien Mitte`` case really is a fake route, but a
   real Distant ↔ Distant connection like ``Mürzzuschlag ↔
   Payerbach-Reichenau`` is also "both unknown" — and dropping it
   forced the message into the fall-through path where bug J kicked in.

The fix replaces the both-unknown heuristic with a more precise
"non-station first word" check (``Bahnsteig``, ``Gleis``, ``Wagen``,
``Aufzug``, …) so real distant routes stay in the route list and get
rejected by the strict classifier, while facility-internal references
still fall through.
"""

from __future__ import annotations

from src.providers.oebb import (
    _extract_routes,
    _find_stations_in_text,
    _is_relevant,
    _looks_like_facility_endpoint,
)
from src.utils.stations import canonical_name


class TestVorAliasFalsePositive:
    """Bug J: the German preposition 'vor' must not alias-match a Wien
    station via the directory's '(VOR)'-suffixed aliases."""

    def test_vor_token_not_in_find_stations(self) -> None:
        # Standalone 'vor' inside a real ÖBB sentence used to surface
        # 'Wien Hauptbahnhof'.
        text = (
            "Wir empfehlen Reisenden, sich vor Reiseantritt mit dem "
            "Kundenservice in Verbindung zu setzen."
        )
        assert "Wien Hauptbahnhof" not in _find_stations_in_text(text)

    def test_vor_in_non_route_message_does_not_force_relevance(self) -> None:
        # Distant-only message that contains the German preposition 'vor'
        # — the message must stay rejected.
        title = "Bauarbeiten: Mürzzuschlag Payerbach-Reichenau"
        desc = (
            "Wegen Bauarbeiten zwischen Mürzzuschlag Bahnhof und "
            "Payerbach-Reichenau Bahnhof. Wir empfehlen Reisenden, sich "
            "vor Reiseantritt mit dem Kundenservice in Verbindung zu "
            "setzen."
        )
        assert _is_relevant(title, desc) is False


class TestDistantRouteRejection:
    """Bug K: real Distant ↔ Distant routes must be rejected by the
    strict route classifier rather than slipping into the fall-through."""

    def test_muerzzuschlag_payerbach_rejected(self) -> None:
        title = "Bauarbeiten: Payerbach-Reichenau an der Rax Mürzzuschlag"
        desc = (
            "Wegen Bauarbeiten können zwischen Payerbach-Reichenau an "
            "der Rax Bahnhof und Mürzzuschlag Bahnhof von 04.10.2026 "
            "(01:00 Uhr) bis 29.11.2026 (05:00 Uhr) einige "
            "Nahverkehrszüge nicht fahren."
        )
        # Route is recognised — it just isn't Wien-relevant.
        routes = _extract_routes(title, desc)
        assert routes
        assert _is_relevant(title, desc) is False

    def test_muerzzuschlag_payerbach_full_payload_rejected(self) -> None:
        # The exact failing description from the live ÖBB cache.
        title = "Bauarbeiten: Mürzzuschlag Payerbach-Reichenau an der Rax"
        desc = (
            "04.10.2026 - 29.11.2026<br/><br/>Wegen Bauarbeiten können<br>"
            "zwischen <b>Mürzzuschlag Bahnhof</b> und "
            "<b>Payerbach-Reichenau an der Rax Bahnhof</b><br>am "
            "<span><b>04.10.2026</b> (01:00 Uhr - 05:00 Uhr),<br>am "
            "<b>08.11.2026</b> (01:00 Uhr - 05:00 Uhr) und<br>am "
            "<b>29.11.2026</b> (01:00 Uhr - 05:00 Uhr)</span> einige "
            "Nahverkehrszüge nicht fahren. Wir empfehlen Reisenden, sich "
            "vor Reiseantritt mit dem Kundenservice in Verbindung zu "
            "setzen."
        )
        assert _is_relevant(title, desc) is False


class TestFacilityHeuristic:
    """The replacement for the both-unknown filter must keep working for
    fake routes (platform/track references) while letting real station
    routes through to the strict classifier."""

    def test_aufzug_between_platforms_drops_route_candidate(self) -> None:
        # Bug D regression: the message must still pick up Wien Mitte
        # via the single-station fall-through.
        title = "Aufzug Wien Mitte"
        desc = "Aufzug zwischen Bahnsteig 1 und Bahnsteig 5 in Wien Mitte defekt"
        assert _extract_routes(title, desc) == []
        assert _is_relevant(title, desc) is True

    def test_facility_endpoint_detection(self) -> None:
        # Single-word and "<word> <number>" forms both classify as facility.
        assert _looks_like_facility_endpoint("Bahnsteig 5")
        assert _looks_like_facility_endpoint("Gleis 12")
        assert _looks_like_facility_endpoint("Aufzug")
        assert _looks_like_facility_endpoint("Wagen 3")
        # Real station-shaped names must NOT trip the heuristic.
        assert not _looks_like_facility_endpoint("Wien Mitte")
        assert not _looks_like_facility_endpoint("Mürzzuschlag")
        assert not _looks_like_facility_endpoint("Payerbach-Reichenau an der Rax")

    def test_canonical_name_for_vor_token_still_returns_wien(self) -> None:
        # Documents the upstream directory state — the fix is the
        # single-token skip in _find_stations_in_text, NOT removing the
        # alias. If a future stations.json cleanup removes the alias the
        # test here can be updated; until then we lock the behaviour in.
        assert canonical_name("vor") == "Wien Hauptbahnhof"
