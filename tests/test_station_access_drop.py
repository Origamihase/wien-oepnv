"""A closed station access is handled like a broken lift (2026-10-09).

Operator decision: "Eine Aufgangssperre soll bitte wie ein defekter Aufzug
behandelt werden." The trains still stop, passengers take another exit, so
the notice has no slot on the info displays. The titles below are every
access closure WL sent since February 2026 (cache and ``data/raw/wl``
history); ÖBB sent none.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from src.providers import wl_fetch
from src.providers.oebb import _is_facility_or_weather_only
from src.utils import raw_capture
from src.utils.text import is_station_access_only


@pytest.mark.parametrize(
    "title",
    [
        "U1: Nestroyplatz, Aufgangssperre ab 12.10.2026",
        "U1: Nestryplatz, Aufgangssperre ab 12.10.2026",
        "U1: Keplerplatz, Sperre eines Aufgangs",
        "U1: Sperre Ausgang beim Keplerplatz",
        "U6: Westbahnhof, Zugang Felberstraße gesperrt",
        "U3: Volkstheater, Eingang Museumstraße gesperrt",
        "U4: Pilgramgasse, Aufgänge gesperrt",
        "U2: Rathaus, Stiegenabgang gesperrt",
    ],
)
def test_access_closure_titles_drop(title: str) -> None:
    assert is_station_access_only(title) is True
    assert wl_fetch._facility_drop_reason(title) == "nur Aufgang/Ausgang"


@pytest.mark.parametrize(
    "title",
    [
        # A measure for the trains keeps the item.
        "U1: Nestroyplatz, Aufgang gesperrt, Züge fahren durch",
        "U3: Volkstheater, Ausgang gesperrt, kein Halt",
        "U6: Sperre Zugang, Ersatzverkehr zwischen Spittelau und Floridsdorf",
        # No access word at all.
        "U1: Nestroyplatz, Station gesperrt",
        "U1: Klapprampensperre am 10.10.2026",
        "13A: Haltestellenverlegung Kliebergasse",
        # A landslide is a rail closure cause, not a station exit.
        "Murenabgang: Strecke Wien-Mödling gesperrt",
        # ÖBB's words for the trains (check of 2026-10-09): "Zugangebot"
        # holds "Zugang", "Ausgangsbahnhof" holds "Ausgang"; the title is
        # about the trains and stays.
        "Geändertes Zugangebot: Wien Hbf",
        "Eingeschränktes Zugangebot zwischen Wien Meidling und Mödling",
        "Zugausfall ab Ausgangsbahnhof Wien Hbf",
        "Züge fallen aus, Ausgang Bahnsteig 1 gesperrt: Wien Meidling",
        "Verspätungen wegen Sperre eines Zugangs: Wien Floridsdorf",
        "Fahrplanänderung, Zugang Bahnsteig 2 gesperrt: Wien Mitte",
        "Eingleisiger Betrieb, Ausgang gesperrt: Wien Simmering",
        "Streckensperre, Zugang gesperrt: Wien Liesing",
        "",
    ],
)
def test_other_titles_stay(title: str) -> None:
    assert is_station_access_only(title) is False


def test_lift_reason_wins() -> None:
    title = "U2: Rathaus - Kein Aufzug am Bahnsteig, Zugang Felderstraße"
    assert wl_fetch._facility_drop_reason(title) == "nur Aufzug/Fahrtreppe"
    assert wl_fetch._facility_drop_reason("U1: Verkehrsunfall") is None


def test_oebb_access_closure_drops_like_a_lift() -> None:
    assert _is_facility_or_weather_only("Wien Meidling: Aufgang Bahnsteig 1 gesperrt", "") is True
    assert _is_facility_or_weather_only("Murenabgang: Strecke Wien-Mödling gesperrt", "") is False
    assert _is_facility_or_weather_only("Wien Hbf: Zugang gesperrt, Züge halten nicht", "") is False
    # ÖBB's train words keep the item: "Zugangebot" reads as "Zugang".
    assert _is_facility_or_weather_only("Geändertes Zugangebot: Wien Hbf", "") is False


def _wl_news(name: str, title: str, description: str, now: datetime) -> dict[str, Any]:
    # Real WL newsList shape (data/raw/wl/newsList.json, N20261005-6221).
    fmt = "%Y-%m-%dT%H:%M:%S.000+0000"
    return {
        "description": description,
        "id": name,
        "location": {
            "properties": {"attributes": {}, "coordName": "WGS84", "type": "news"},
            "type": "Feature",
        },
        "name": name,
        "refPoiCategoryId": 13296,
        "relatedLines": ["U1"],
        "relatedStops": [4115, 4114],
        "time": {
            "end": (now + timedelta(days=50)).strftime(fmt),
            "start": (now - timedelta(days=4)).strftime(fmt),
            "validFrom": (now - timedelta(days=4)).strftime(fmt),
        },
        "title": title,
    }


def test_wl_fetch_drops_the_aufgangssperre(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(raw_capture, "RAW_ROOT", tmp_path / "raw")
    monkeypatch.setenv(raw_capture.RAW_CAPTURE_ENV, "1")
    now = datetime.now(UTC)
    pois = [
        _wl_news(
            "N20261005-6221",
            "U1: Nestroyplatz, Aufgangssperre ab 12.10.2026",
            "<h2>Aufgangssperre</h2>\r\n<p>Wegen Sanierungsarbeiten wird in der Station "
            "Nestroyplatz ein Aufgang gesperrt.</p>",
            now,
        ),
        _wl_news(
            "N20261005-6230",
            "U1: Nestroyplatz, kein Halt",
            "<p>Wegen Bauarbeiten fahren die Züge der Linie U1 in der Station "
            "Nestroyplatz ohne Halt durch.</p>",
            now,
        ),
    ]
    responses = {
        "trafficInfoList": {"data": {"trafficInfos": []}, "message": {"value": "OK"}},
        "newsList": {"data": {"pois": pois}, "message": {"value": "OK"}},
    }
    monkeypatch.setattr(wl_fetch, "_get_json", lambda path, **kwargs: responses[path])

    items = wl_fetch.fetch_events()

    assert [it["title"] for it in items] == ["U1: Nestroyplatz, kein Halt"]
    assert {"grund": "nur Aufgang/Ausgang", "titel": "U1: Nestroyplatz, Aufgangssperre ab 12.10.2026"} in (
        raw_capture.collected_drops("wl")
    )
