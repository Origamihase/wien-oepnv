"""Tests for the Baustellen ÖPNV-relevance filter.

The provider must only surface construction sites at/near a rail Bahnhof
(Wien station or Pendlerbahnhof); ordinary road works anywhere else in
the city must be dropped so the feed stays a focused transit signal.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts import update_baustellen_cache
from src.build_feed import _post_filter_baustellen
from src.providers import baustellen
from src.providers.baustellen import (
    DEFAULT_STATION_RADIUS_M,
    is_transit_relevant,
    mentions_oepnv,
    oepnv_lead,
    relevant_station,
    u_bahn_lines,
)
from src.utils import stations

# Real directory coordinates (data/stations.json).
_STATIONS_JSON = Path(__file__).resolve().parents[1] / "data" / "stations.json"


def _directory_coord(name: str) -> tuple[float, float]:
    """Return a station's authoritative ``(lat, lon)`` from the committed
    directory.

    Derived rather than hard-coded so the fixture tracks consensus-driven
    coordinate updates — the WL→HAFAS→OSM reconciliation re-points
    multimodal hubs such as Hauptbahnhof to the WL/OSM-agreed position —
    instead of pinning a value that silently drifts out of the matching
    radius.
    """
    payload = json.loads(_STATIONS_JSON.read_text(encoding="utf-8"))
    entries = payload["stations"] if isinstance(payload, dict) else payload
    for entry in entries:
        if entry.get("name") == name:
            return (float(entry["latitude"]), float(entry["longitude"]))
    raise AssertionError(f"{name!r} not found in {_STATIONS_JSON}")


WIEN_HBF = _directory_coord("Wien Hauptbahnhof")  # multimodal hub, in_vienna
MOEDLING = (48.085628, 16.295474)  # bst_id 1377, pendler
# A point deep in the Donau-Auen floodplain — no rail Bahnhof for km.
FAR_AWAY = (48.170000, 16.520000)

SAMPLE_PATH = Path(__file__).resolve().parents[1] / "data" / "samples" / "baustellen_sample.geojson"

_RailSet = tuple[tuple[str, float, float], ...]


def _loc(lat: float, lon: float) -> dict[str, Any]:
    return {"address": "Teststraße", "coordinates": {"lat": lat, "lon": lon}}


@pytest.fixture
def single_station(monkeypatch: pytest.MonkeyPatch) -> _RailSet:
    """Replace the rail-station set with one synthetic Bahnhof so distance
    assertions are independent of the real directory."""

    station = (("Test Bahnhof", 48.2000, 16.3700),)
    monkeypatch.setattr(stations, "_rail_station_coordinates", lambda: station)
    return station


# --- nearest_rail_station: geometry / radius ----------------------------------


def test_nearest_rail_station_matches_at_zero_distance(single_station: _RailSet) -> None:
    match = stations.nearest_rail_station(48.2000, 16.3700, 150.0)
    assert match is not None
    assert match[0] == "Test Bahnhof"
    assert match[1] == pytest.approx(0.0, abs=1.0)


def test_nearest_rail_station_within_radius(single_station: _RailSet) -> None:
    # ~100 m north of the station (0.0009° lat ≈ 100 m).
    assert stations.nearest_rail_station(48.2009, 16.3700, 150.0) is not None


def test_nearest_rail_station_outside_radius(single_station: _RailSet) -> None:
    # ~300 m north — dropped at 150 m, kept once the radius is widened.
    assert stations.nearest_rail_station(48.2027, 16.3700, 150.0) is None
    assert stations.nearest_rail_station(48.2027, 16.3700, 500.0) is not None


@pytest.mark.parametrize(
    "lat, lon",
    [
        (None, 16.37),
        (48.2, None),
        (float("nan"), 16.37),
        (48.2, float("inf")),
        (95.0, 16.37),  # latitude out of European coercion range
        ("x", 16.37),
    ],
)
def test_nearest_rail_station_fails_closed_on_bad_coords(
    single_station: _RailSet, lat: object, lon: object
) -> None:
    assert stations.nearest_rail_station(lat, lon, 150.0) is None


def test_nearest_rail_station_rejects_nonpositive_radius(single_station: _RailSet) -> None:
    assert stations.nearest_rail_station(48.2000, 16.3700, 0.0) is None
    assert stations.nearest_rail_station(48.2000, 16.3700, -10.0) is None


# --- is_transit_relevant / relevant_station -----------------------------------


def test_relevant_station_at_real_bahnhof() -> None:
    name = relevant_station(_loc(*WIEN_HBF))
    assert name is not None
    assert "Hauptbahnhof" in name


def _item(lat: float, lon: float, *, title: str = "Sanierung", description: str = "Fahrbahn") -> dict[str, Any]:
    return {"location": _loc(lat, lon), "title": title, "description": description}


def test_geo_only_item_is_relevant_without_oepnv_text() -> None:
    # Near a Pendlerbahnhof, no ÖPNV keyword needed.
    assert is_transit_relevant(_item(*MOEDLING)) is True


def test_text_only_item_is_relevant_far_from_rail() -> None:
    item = _item(*FAR_AWAY, title="Umbau", description="Die Haltestelle wird verlegt")
    assert is_transit_relevant(item) is True


def test_item_neither_geo_nor_oepnv_is_not_relevant() -> None:
    assert is_transit_relevant(_item(*FAR_AWAY, title="Innenhof", description="Hinterhofarbeiten")) is False


@pytest.mark.parametrize(
    "item",
    [
        None,
        "nope",
        {},
        {"title": "Innenhof", "description": "Hinterhofarbeiten"},  # no location, no ÖPNV text
        {"location": {"coordinates": {"lat": None, "lon": None}}, "title": "x", "description": "y"},
        {"location": {"coordinates": {"lat": float("nan"), "lon": 16.37}}, "title": "x", "description": "y"},
    ],
)
def test_is_transit_relevant_fails_closed(item: object) -> None:
    assert is_transit_relevant(item) is False


def test_radius_override_widens_geo_match(
    monkeypatch: pytest.MonkeyPatch, single_station: _RailSet
) -> None:
    # ~300 m from the synthetic station, no ÖPNV text → relevance is purely radius-driven.
    far = _item(48.2027, 16.3700, title="Sanierung", description="Fahrbahn")
    assert is_transit_relevant(far) is False
    monkeypatch.setenv("BAUSTELLEN_STATION_RADIUS_M", "500")
    assert is_transit_relevant(far) is True


# --- mentions_oepnv -----------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Die Haltestelle Haspingerplatz wird aufgelassen",
        "Umleitung der Straßenbahnlinie 2",
        "Schienenersatzverkehr eingerichtet",
        "betrifft die öffentlichen Verkehrsmittel",
        "Sperre der Buslinie 10A",
        "Linie 46 verkürzt",
        "U6 Teilsperre",
        # bug b4: the leading-only \b must KEEP the compound U-/S-Bahn forms
        # (no boundary exists between "bahn" and "bau"/"station").
        "U-Bahnbau wird gesperrt",
        "Neubau der U-Bahnstation der U2",
        "S-Bahn-Stammstrecke betroffen",
    ],
)
def test_mentions_oepnv_true(text: str) -> None:
    assert mentions_oepnv(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Vollsperre der Fahrbahn wegen Rohrlegung",
        "Gehsteigsanierung im Innenhof",
        "Buschenschank am Nussberg",  # 'Busch…' must not trip the \\bbus\\b token
        # bug b4: the substrings "ubahn" / "sbahn" inside unrelated compounds
        # must NOT trip the U-/S-Bahn tokens (leading \b on both).
        "Fahrbahnsanierung bei der Hochschaubahn",
        "Erneuerung am Verkehrsbahnhof Inzersdorf",
    ],
)
def test_mentions_oepnv_false(text: str) -> None:
    assert mentions_oepnv(text) is False


# --- radius resolution / clamping ---------------------------------------------


def test_resolve_radius_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BAUSTELLEN_STATION_RADIUS_M", raising=False)
    assert baustellen._resolve_radius_m() == DEFAULT_STATION_RADIUS_M


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("500", 500.0),
        ("5", 25.0),  # clamped up to the minimum
        ("999999", 2000.0),  # clamped down to the maximum
        ("abc", DEFAULT_STATION_RADIUS_M),
        ("inf", DEFAULT_STATION_RADIUS_M),
        ("  ", DEFAULT_STATION_RADIUS_M),
    ],
)
def test_resolve_radius_clamping(monkeypatch: pytest.MonkeyPatch, raw: str, expected: float) -> None:
    monkeypatch.setenv("BAUSTELLEN_STATION_RADIUS_M", raw)
    assert baustellen._resolve_radius_m() == expected


# --- _post_filter_baustellen --------------------------------------------------


def test_post_filter_keeps_relevant_drops_noise_passes_stubs() -> None:
    geo = {"title": "Fahrbahnsanierung", "description": "x", "location": _loc(*WIEN_HBF)}
    text = {"title": "Gleisarbeiten", "description": "Haltestelle verlegt", "location": _loc(*FAR_AWAY)}
    noise = {"title": "Hinterhof", "description": "Innenhofarbeiten", "location": _loc(*FAR_AWAY)}
    stub = {"guid": "meta-only"}
    sentinel = "passthrough-non-dict"

    result = _post_filter_baustellen([geo, text, noise, stub, sentinel])

    titles = [r["title"] for r in result if isinstance(r, dict) and "title" in r]
    # geo-relevant → kept and prefixed with the affected Bahnhof.
    assert any(t.endswith("Fahrbahnsanierung") and "Hauptbahnhof" in t for t in titles)
    # text-relevant (far from rail, but mentions a stop) → kept, NOT prefixed.
    assert "Gleisarbeiten" in titles
    assert "Hinterhof" not in titles  # neither near a Bahnhof nor ÖPNV text → dropped
    assert stub in result  # title/description-less stub passes through
    assert sentinel in result  # non-dict passes through


def test_post_filter_enriches_title_with_affected_bahnhof() -> None:
    item = {"title": "Vollsperre Nordbahnstraße", "description": "x", "location": _loc(*WIEN_HBF)}
    [out] = _post_filter_baustellen([item])
    assert out["title"].startswith("Wien Hauptbahnhof: ")
    assert out["title"].endswith("Vollsperre Nordbahnstraße")
    # Original dict is left untouched (mutation via copy).
    assert item["title"] == "Vollsperre Nordbahnstraße"


def test_post_filter_does_not_double_name_station() -> None:
    # Title already names the station → no redundant prefix.
    item = {"title": "Umbau Bahnhof Mödling", "description": "x", "location": _loc(*MOEDLING)}
    [out] = _post_filter_baustellen([item])
    assert out["title"] == "Umbau Bahnhof Mödling"


def test_post_filter_prefixes_u_bahn_line_when_not_geo() -> None:
    item = {
        "title": "Neubaugasse",
        "description": "Für den U-Bahnbau der U2 / U5 wird gesperrt.",
        "location": _loc(*FAR_AWAY),
    }
    [out] = _post_filter_baustellen([item])
    assert out["title"] == "U2/U5: Neubaugasse"


def test_post_filter_does_not_double_name_u_bahn_line() -> None:
    # Title already names the U-Bahn line → no redundant "U2:" prefix.
    item = {
        "title": "U2 Rathaus gesperrt",
        "description": "Für den U-Bahnbau der U2 wird gesperrt.",
        "location": _loc(*FAR_AWAY),
    }
    [out] = _post_filter_baustellen([item])
    assert out["title"] == "U2 Rathaus gesperrt"


def test_post_filter_does_not_guess_a_bus_tram_line() -> None:
    # Bus/tram impact → kept, but NO guessed line prefix (only U-Bahn/Bahnhof).
    item = {
        "title": "Eßlinger Hauptstraße 96",
        "description": "Der Busverkehr der Wiener Linien wird umgeleitet.",
        "location": _loc(*FAR_AWAY),
    }
    [out] = _post_filter_baustellen([item])
    assert out["title"] == "Eßlinger Hauptstraße 96"


# --- u_bahn_lines / oepnv_lead ------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Bauvorhaben U2 / U5", ["U2", "U5"]),
        ("Neubau der U-Bahnstation der U2 Pilgramgasse", ["U2"]),
        ("Generalsanierung der U6 Trasse", ["U6"]),
        ("U-Bahnbau ohne Liniennummer", []),
        ("Buslinie 10A und Hausnummer 96", []),
        ("", []),
    ],
)
def test_u_bahn_lines(text: str, expected: list[str]) -> None:
    assert u_bahn_lines(text) == expected


def test_oepnv_lead_moves_transit_sentence_to_front() -> None:
    text = "Die Gleisbauarbeiten erfolgen bei Tag und Nacht. Die Haltestelle der Linie 2 wird verlegt."
    assert oepnv_lead(text).startswith("Die Haltestelle der Linie 2 wird verlegt.")


def test_oepnv_lead_noop_when_already_leading_or_no_match() -> None:
    leads = "Die Haltestelle wird verlegt. Danach normaler Betrieb."
    assert oepnv_lead(leads) == leads
    no_oepnv = "Rohrlegung bei Tag. Keine Behinderung des Verkehrs."
    assert oepnv_lead(no_oepnv) == no_oepnv
    assert oepnv_lead("") == ""


def test_oepnv_lead_keeps_abbreviation_period_intact() -> None:
    # bug b5: "Nr." is an abbreviation, not a sentence end — the stop number
    # stays attached when the ÖPNV sentence is surfaced (was mangled to
    # "Die Haltestelle Nr. <other sentence> 4351 …").
    out = oepnv_lead("Fahrbahnerneuerung. Die Haltestelle Nr. 4351 wird verlegt.")
    assert out.startswith("Die Haltestelle Nr. 4351 wird verlegt.")


def test_oepnv_lead_keeps_date_ordinal_intact() -> None:
    # bug b5: the day ordinal "3." must not split "3. März".
    out = oepnv_lead("Ab 3. März gilt eine Umleitung. Die Buslinie wird verlegt.")
    assert out.startswith("Die Buslinie wird verlegt.")
    assert "3. März" in out


def test_oepnv_lead_still_splits_lowercase_next_sentence() -> None:
    # No uppercase-follower gate: a real boundary before a lowercase-initial
    # next sentence is still split, so the ÖPNV sentence is surfaced.
    out = oepnv_lead("Fahrbahn wird saniert. die Haltestelle wird verlegt.")
    assert out.startswith("die Haltestelle wird verlegt.")


def test_sample_linestring_description_leads_with_oepnv() -> None:
    payload = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    events = update_baustellen_cache._collect_events(payload)
    # Feature 2 (Thaliastraße): the ÖPNV sentence must lead the description.
    assert events[1]["description"].startswith(
        "Die Haltestelle der Straßenbahnlinie 2 wird verlegt"
    )


# --- _first_lonlat (geometry descent) -----------------------------------------


@pytest.mark.parametrize(
    "coordinates, expected",
    [
        ([16.4, 48.2], (16.4, 48.2)),  # Point
        ([[16.4, 48.2], [16.5, 48.3]], (16.4, 48.2)),  # LineString
        ([[[16.4, 48.2], [16.5, 48.3]]], (16.4, 48.2)),  # Polygon ring
        ([[[[16.4, 48.2]]]], (16.4, 48.2)),  # MultiPolygon
    ],
)
def test_first_lonlat_descends_geometries(
    coordinates: object, expected: tuple[float, float]
) -> None:
    assert update_baustellen_cache._first_lonlat(coordinates) == expected


@pytest.mark.parametrize(
    "coordinates",
    [
        None,
        [],
        [16.4],  # too short
        [True, False],  # bools are not coordinates
        "16.4,48.2",
        [[[[[[[[[[16.4, 48.2]]]]]]]]]],  # deeper than _MAX_COORD_DEPTH
    ],
)
def test_first_lonlat_rejects_bad_geometries(coordinates: object) -> None:
    assert update_baustellen_cache._first_lonlat(coordinates) is None


# --- end-to-end on the bundled sample -----------------------------------------


def test_sample_payload_is_all_transit_relevant() -> None:
    payload = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    events = update_baustellen_cache._collect_events(payload)
    assert len(events) == 2
    # Feature 1 is geo-relevant (at a Bahnhof), feature 2 is text-relevant
    # (mentions a stop, far from rail) — the "Bahnhofsnähe ODER ÖPNV-Text" policy.
    assert all(is_transit_relevant(event) for event in events)
    # The LineString feature must still yield a usable representative coordinate.
    assert events[1]["location"]["coordinates"]["lat"] == pytest.approx(48.2103)
