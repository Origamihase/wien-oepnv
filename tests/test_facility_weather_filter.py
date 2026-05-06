"""User-spec: facility-only and weather-only messages have no place in
the feed.

> Defekte Aufzüge oder die Wetterlage hat im Feed nichts zu suchen.
> Im Feed soll es um Störungen des ÖPNVs in Wien und deren Pendler gehen.

The filter must therefore drop messages whose primary topic is a broken
facility (Aufzug, Lift, Fahrtreppe, Rolltreppe) or a standalone weather
warning (Sturm, Wetterlage, …) — even when a Wien station is mentioned.
Mixed messages, where the title also carries a real transit keyword
(Bauarbeiten, Störung, Verspätung, Sperre, …), still pass through so
the route- and station-level checks can take over.
"""

from __future__ import annotations

from src.providers.oebb import _is_facility_or_weather_only, _is_relevant


class TestUserReportedFacilityWeatherDrops:
    def test_aufzug_defekt_wien_hbf_dropped(self) -> None:
        assert _is_relevant("Aufzug defekt: Wien Hauptbahnhof", "x") is False

    def test_sturm_im_raum_wien_dropped(self) -> None:
        assert (
            _is_relevant("Sturm im Raum Wien", "Verzögerungen bei der S-Bahn Wien.")
            is False
        )

    def test_wetterlage_dropped(self) -> None:
        assert _is_relevant("Wetterlage Wien", "Hinweis") is False

    def test_lift_outage_dropped(self) -> None:
        assert _is_relevant("Lift außer Betrieb Wien Hbf", "x") is False

    def test_fahrtreppe_dropped(self) -> None:
        assert _is_relevant("Fahrtreppe defekt Wien Mitte", "x") is False


class TestRealDisruptionsStillKept:
    def test_bauarbeiten_wien_kept(self) -> None:
        assert _is_relevant("Bauarbeiten Wien Hauptbahnhof", "x") is True

    def test_stoerung_wien_kept(self) -> None:
        assert _is_relevant("Störung Wien Hauptbahnhof", "x") is True

    def test_route_message_kept(self) -> None:
        assert _is_relevant(
            "Bauarbeiten: Wien Hauptbahnhof ↔ Mödling", "x"
        ) is True

    def test_weather_caused_route_disruption_kept(self) -> None:
        # Mixed: weather-caused but transit-described — keep.
        assert (
            _is_relevant(
                "Sturmschaden: Strecke Wien - Mödling gesperrt",
                "Wegen Sturm kein Verkehr zwischen Wien Hbf und Mödling.",
            )
            is True
        )

    def test_aufzug_betroffen_with_bauarbeiten_dropped(self) -> None:
        # Per spec the ANY mention of a facility keyword in the title
        # drops the message — even when "Bauarbeiten" is also present,
        # because the affected facility is still the actual subject.
        assert _is_relevant(
            "Bauarbeiten Wien Hbf - Aufzug betroffen", "x"
        ) is False


class TestFacilityWeatherFunctionDirectly:
    def test_pure_facility_title(self) -> None:
        assert _is_facility_or_weather_only("Aufzug defekt: Wien Hauptbahnhof", "")

    def test_pure_weather_title(self) -> None:
        assert _is_facility_or_weather_only("Sturmwarnung im Raum Wien", "")

    def test_mixed_facility_with_bauarbeiten_still_drops(self) -> None:
        # Strict facility rule: any title mention of "Aufzug" drops.
        assert _is_facility_or_weather_only(
            "Bauarbeiten Wien Hbf - Aufzug betroffen", ""
        )

    def test_mixed_weather_with_bauarbeiten_kept(self) -> None:
        # Weather is more lenient: a transit keyword in the title
        # rescues a Sturm-caused service disruption.
        assert not _is_facility_or_weather_only(
            "Sturmschaden: Strecke Wien-Mödling gesperrt", ""
        )

    def test_no_facility_no_weather(self) -> None:
        assert not _is_facility_or_weather_only(
            "Bauarbeiten: Wien Hauptbahnhof ↔ Mödling", ""
        )

    def test_empty_title(self) -> None:
        assert not _is_facility_or_weather_only("", "")


class TestPluralFormsAndWeatherCause:
    """Bug Q: the transit-keyword regex used \\b...\\b which missed German
    plural / inflected forms (Verspätung → Verspätungen, Sperrung →
    Sperrungen, …). Combined with the title-residual heuristic that
    treated the German weather noun "Sturm" as an unknown second
    endpoint, weather-caused real disruptions were dropped.
    """

    def test_verspaetungen_plural_keeps_with_weather_cause(self) -> None:
        assert _is_relevant("Verspätungen Wien Hbf wegen Sturm", "x") is True

    def test_sperrungen_plural_keeps(self) -> None:
        assert (
            _is_relevant(
                "Sperrungen zwischen Wien Hbf und Mödling am Wochenende",
                "Wegen Sturm.",
            )
            is True
        )

    def test_stoerungen_plural_keeps(self) -> None:
        assert _is_relevant("Störungen wegen Sturm in Wien Hbf", "x") is True

    def test_gesperrt_keeps(self) -> None:
        assert (
            _is_relevant(
                "Sturmschaden zwischen Wien und Mödling — Strecke gesperrt",
                "Wegen Sturm gesperrt zwischen Wien Hbf und Mödling.",
            )
            is True
        )

    def test_weather_only_still_drops(self) -> None:
        # Sanity: pure weather (no transit keyword) still drops.
        assert _is_relevant("Sturm im Raum Wien", "x") is False
        assert _is_relevant("Wetterlage Wien", "x") is False
