"""Regression tests for the user-reported Payerbach-Mürzzuschlag leak.

The live ÖBB cache contained:

    Bauarbeiten: Mürzzuschlag Payerbach-Reichenau an der Rax
    Bauarbeiten: Payerbach-Reichenau an der Rax Mürzzuschlag

Both describe a Distant ↔ Distant route (neither station is in Vienna or
on the Pendler whitelist) and must NOT appear in the feed.

Two bugs combined to let the first variant slip through:

J. ``canonical_name("vor")`` returned ``Wien Hauptbahnhof`` because
   ``Hbf (VOR)``-style aliases in stations.json normalised down to a
   single ``"vor"`` key. The German preposition ``vor`` (e.g. "vor
   Reiseantritt") therefore aliased to a flagship Wien station and
   tripped the single-station fall-through path. The directory side has
   since been cleaned up upstream, but ``vor`` is still on the
   single-token skip list as defence in depth in case the alias
   re-enters via a future regeneration run.

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

    def test_facility_route_candidate_dropped(self) -> None:
        # Bug D regression: the fake "zwischen Bahnsteig 1 und Bahnsteig
        # 5" route must be dropped from the candidate list. The new
        # facility/weather filter then drops the message itself because
        # an "Aufzug"-titled notice without a transit keyword is
        # facility-only per project spec.
        title = "Aufzug Wien Mitte"
        desc = "Aufzug zwischen Bahnsteig 1 und Bahnsteig 5 in Wien Mitte defekt"
        assert _extract_routes(title, desc) == []
        assert _is_relevant(title, desc) is False

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

    def test_vor_token_skipped_by_find_stations(self) -> None:
        # Defence in depth: even if a future stations.json regeneration
        # re-introduces an "Hbf (VOR)" alias that normalises to "vor",
        # _find_stations_in_text must NOT surface a Wien station for the
        # bare preposition "vor".
        assert _find_stations_in_text("Wir empfehlen, sich vor der Reise zu informieren.") == []
