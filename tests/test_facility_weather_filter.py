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

    def test_aufzug_betroffen_with_bauarbeiten_kept(self) -> None:
        # Mixed: title primary subject is Bauarbeiten, Aufzug is collateral.
        assert _is_relevant(
            "Bauarbeiten Wien Hbf - Aufzug betroffen", "x"
        ) is True


class TestFacilityWeatherFunctionDirectly:
    def test_pure_facility_title(self) -> None:
        assert _is_facility_or_weather_only("Aufzug defekt: Wien Hauptbahnhof", "")

    def test_pure_weather_title(self) -> None:
        assert _is_facility_or_weather_only("Sturmwarnung im Raum Wien", "")

    def test_mixed_with_bauarbeiten(self) -> None:
        # "Bauarbeiten" is a real transit keyword → not facility-only.
        assert not _is_facility_or_weather_only(
            "Bauarbeiten Wien Hbf - Aufzug betroffen", ""
        )

    def test_no_facility_no_weather(self) -> None:
        assert not _is_facility_or_weather_only(
            "Bauarbeiten: Wien Hauptbahnhof ↔ Mödling", ""
        )

    def test_empty_title(self) -> None:
        assert not _is_facility_or_weather_only("", "")
