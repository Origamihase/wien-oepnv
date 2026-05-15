"""Regression test for Bug 31A (compound facility nouns escape the drop filter).

User feedback: three real ÖBB lift-failure meldungen reached the feed::

    Technische Störung des Personenlift in Gramatneusiedl: Bahnsteig 3/4
    Technische Störung des Personenlift in Bad Vöslau: Bahnsteig 1
    Technische Störung des Personenlift in Stockerau: Bahnsteig 1/2

These describe broken passenger lifts (Personenlift) — pure facility
notices that the project spec explicitly excludes ("Defekte Aufzüge
oder die Wetterlage hat im Feed nichts zu suchen").

The previous ``_FACILITY_KEYWORD_RE`` pattern ``\\b(...|lift|...)\\b``
used word boundaries on both sides, so it caught the bare ``Lift`` /
``Aufzug`` forms but missed compound German nouns like:

* ``Personenlift`` — passenger lift
* ``Aufzugsstörung`` — lift fault
* ``Liftanlage`` — lift installation

Real German transit text uses these compound forms constantly. The
fix wraps each root with ``\\w*`` so the regex catches both standalone
and compound variants.
"""

from __future__ import annotations

from src.providers.oebb import _is_facility_or_weather_only, _is_relevant
from src.providers.wl_text import _is_facility_only


class TestOebbCompoundFacilityDropped:
    def test_personenlift_gramatneusiedl(self) -> None:
        title = (
            "Technische Störung des Personenlift in Gramatneusiedl: "
            "Bahnsteig 3/4"
        )
        desc = (
            "Wegen einer technischen Störung ist derzeit der "
            "Personenlift zu Bahnsteig 3/4 in Gramatneusiedl Bahnhof "
            "außer Betrieb und kann daher nicht benutzt werden."
        )
        assert _is_facility_or_weather_only(title, desc) is True
        assert _is_relevant(title, desc) is False

    def test_personenlift_bad_voeslau(self) -> None:
        title = "Technische Störung des Personenlift in Bad Vöslau: Bahnsteig 1"
        desc = (
            "Wegen einer technischen Störung ist derzeit der "
            "Personenlift zu Bahnsteig 1 in Bad Vöslau Bahnhof außer "
            "Betrieb und kann daher nicht benutzt werden."
        )
        assert _is_relevant(title, desc) is False

    def test_personenlift_stockerau(self) -> None:
        title = "Technische Störung des Personenlift in Stockerau: Bahnsteig 1/2"
        desc = (
            "Wegen einer technischen Störung ist derzeit der "
            "Personenlift zu Bahnsteig 1/2 in Stockerau Bahnhof außer "
            "Betrieb und kann daher nicht benutzt werden."
        )
        assert _is_relevant(title, desc) is False

    def test_compound_aufzugsstoerung(self) -> None:
        # Other compound forms must also drop.
        title = "Aufzugsstörung am Wien Hauptbahnhof"
        assert _is_facility_or_weather_only(title, "") is True

    def test_compound_liftanlage(self) -> None:
        title = "Liftanlage außer Betrieb in Wien"
        assert _is_facility_or_weather_only(title, "") is True


class TestOebbBareFacilityStillDropped:
    """The existing standalone-form behaviour must continue to hold."""

    def test_bare_aufzug(self) -> None:
        assert _is_facility_or_weather_only("Aufzug defekt: Wien Hbf", "") is True

    def test_bare_lift(self) -> None:
        assert _is_facility_or_weather_only("Lift außer Betrieb", "") is True

    def test_rolltreppe(self) -> None:
        assert _is_facility_or_weather_only("Rolltreppe defekt", "") is True


class TestOebbRealDisruptionsNotDropped:
    """Compound-noun matching must not over-strip legitimate disruption titles."""

    def test_bauarbeiten_route_kept(self) -> None:
        title = "Bauarbeiten Wien Hbf ↔ Mödling"
        assert _is_facility_or_weather_only(title, "") is False

    def test_s_bahn_route_kept(self) -> None:
        title = "S 50: Wien Westbahnhof ↔ Wien Hütteldorf"
        assert _is_facility_or_weather_only(title, "") is False


class TestWlCompoundFacilityDropped:
    def test_personenlift_wl(self) -> None:
        assert _is_facility_only("Personenlift defekt am Stephansplatz") is True

    def test_aufzugsstoerung_wl(self) -> None:
        assert _is_facility_only("Aufzugsstörung U1 Stephansplatz") is True

    def test_real_wl_disruption_kept(self) -> None:
        assert (
            _is_facility_only("U6: Verspätung wegen Schadhaftem Fahrzeug")
            is False
        )
