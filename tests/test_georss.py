"""GeoRSS elements in the feed (``src/build_feed.py`` — ``_item_geometry``).

Operator decision 2026-10-16: a point or a line only where the place is
certain, nothing otherwise; coordinates only from open datasets
(``src.utils.stations.open_data_coordinate``).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from src import build_feed as bf
from src.feed_types import FeedItem
from src.utils import stations

GEORSS = "{http://www.georss.org/georss}"
_NOW = datetime(2026, 10, 16, 18, 0, tzinfo=UTC)
_REAL_OPEN_DATA_COORDINATE = stations.open_data_coordinate

_COORDS = {
    "Wien Hauptbahnhof": (48.185201, 16.376296),
    "Felixdorf": (47.886955, 16.247838),
    "Hollabrunn": (48.5601, 16.0789),
    "Wien Meidling": (48.17472, 16.33382),
    "Wien Max-Mauermann-Gasse (WL)": (48.1631127, 16.4093726),
}


@pytest.fixture(autouse=True)
def _open_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixed open-data coordinates, so the tests do not follow directory updates."""

    def fake_coordinate(name: str, *, prefer: str = "oebb") -> stations.OpenCoordinate | None:
        hit = _COORDS.get(name)
        return stations.OpenCoordinate(hit[0], hit[1], "test") if hit else None

    monkeypatch.setattr(stations, "open_data_coordinate", fake_coordinate)
    monkeypatch.setattr(
        stations,
        "station_wl_lines",
        lambda name: frozenset({"15A"}) if name == "Wien Max-Mauermann-Gasse (WL)" else frozenset(),
    )


def _oebb(title: str) -> FeedItem:
    return cast(FeedItem, {"source": "ÖBB", "title": title, "description": "", "link": ""})


def _wl(title: str, description: str) -> FeedItem:
    return cast(
        FeedItem, {"source": "Wiener Linien", "title": title, "description": description, "link": ""}
    )


def _baustelle(lat: float, lon: float) -> FeedItem:
    return cast(
        FeedItem,
        {
            "source": "Stadt Wien – Baustellen",
            "title": "Burggasse 67",
            "description": "",
            "link": "",
            "location": {"coordinates": {"lat": lat, "lon": lon}},
        },
    )


def test_oebb_single_station_is_a_point() -> None:
    assert bf._item_geometry(_oebb("Hollabrunn")) == ((48.5601, 16.0789),)


def test_oebb_section_is_a_two_point_line_without_midpoint() -> None:
    geometry = bf._item_geometry(_oebb("R 95: Wien Hauptbahnhof ↔ Felixdorf"))
    assert geometry == ((48.185201, 16.376296), (47.886955, 16.247838))


def test_oebb_same_station_twice_falls_back_to_a_point() -> None:
    geometry = bf._item_geometry(_oebb("Wien Hauptbahnhof ↔ Wien Hbf"))
    assert geometry == ((48.185201, 16.376296),)


@pytest.mark.parametrize(
    "title",
    [
        "S 80: Wien Liesing ↔ Wien Meidling / Wien Hauptbahnhof ↔ Wien Stadlau",
        "Wien Hauptbahnhof ↔ Wien Liesing ↔ Wien Meidling",
        "S 4: Bauarbeiten: kein Halt in Lind-Rosegg Föderlach",
        "Hinterstoder [in St.Pankraz]",
        "Aufhebung Streckenunterbrechung: Wien Meidling",
        "Wien Hauptbahnhof ↔ Ersatzverkehr",
    ],
)
def test_oebb_uncertain_titles_get_nothing(title: str) -> None:
    assert bf._item_geometry(_oebb(title)) is None


def test_oebb_station_without_open_coordinate_gets_nothing() -> None:
    # Wien Liesing resolves in the directory but has no open coordinate here.
    assert bf._item_geometry(_oebb("Wien Hauptbahnhof ↔ Wien Liesing")) is None


def test_wl_stop_relocation_is_a_point() -> None:
    item = _wl(
        "15A: Max-Mauermann-Gasse",
        "<p>Haltestellenverlegung der Linie 15A in Richtung Bahnhof Meidling S U</p>",
    )
    assert bf._item_geometry(item) == ((48.1631127, 16.4093726),)


def test_wl_relocation_of_a_stop_the_line_does_not_serve_gets_nothing() -> None:
    item = _wl("13A: Max-Mauermann-Gasse", "Haltestellenverlegung der Linie 13A")
    assert bf._item_geometry(item) is None


def test_wl_disruption_gets_nothing_even_with_a_station_in_the_title() -> None:
    item = _wl("6: Polizeieinsatz Betrieb ab Matzleinsdorfer Platz", "Polizeieinsatz")
    assert bf._item_geometry(item) is None


def test_baustelle_uses_its_own_coordinate_inside_vienna() -> None:
    assert bf._item_geometry(_baustelle(48.20428021, 16.34831234)) == ((48.20428021, 16.34831234),)


def test_baustelle_outside_vienna_gets_nothing() -> None:
    assert bf._item_geometry(_baustelle(47.886955, 16.247838)) is None


def _render(items: list[FeedItem]) -> ET.Element:
    xml = bf._make_rss(items, _NOW, {}, lang="de")
    return ET.fromstring(xml)


def test_feed_carries_point_and_line_with_six_decimals_and_namespace() -> None:
    xml = bf._make_rss(
        [_oebb("Hollabrunn"), _oebb("Wien Hauptbahnhof ↔ Felixdorf")], _NOW, {}, lang="de"
    )
    assert 'xmlns:georss="http://www.georss.org/georss"' in xml
    items = ET.fromstring(xml).findall("./channel/item")
    texts = {
        el.tag: el.text for item in items for el in item if el.tag.startswith(GEORSS)
    }
    assert texts == {
        f"{GEORSS}point": "48.560100 16.078900",
        f"{GEORSS}line": "48.185201 16.376296 47.886955 16.247838",
    }


def test_items_without_a_certain_place_carry_no_georss() -> None:
    root = _render([_wl("44: Verkehrsunfall", "Fahrtbehinderung.")])
    assert not [el for el in root.iter() if el.tag.startswith(GEORSS)]


def test_en_feed_carries_the_same_geometry(monkeypatch: pytest.MonkeyPatch) -> None:
    item = _oebb("Wien Hauptbahnhof ↔ Felixdorf")
    monkeypatch.setattr(bf, "_get_translation_pipeline", lambda: None)
    xml = bf._make_rss([item], _NOW, {}, lang="en")
    assert "<georss:line>48.185201 16.376296 47.886955 16.247838</georss:line>" in xml


def test_open_data_coordinate_never_falls_back_to_the_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries: dict[str, dict[str, Any]] = {
        "Rail": {"name": "Rail", "eva_nr": "1", "latitude": 1.0, "longitude": 1.0},
        "Stop": {"name": "Stop", "wl_diva": 60200001, "latitude": 1.0, "longitude": 1.0},
        "Both": {"name": "Both", "eva_nr": "2", "wl_diva": 60200002},
        "Merged": {"name": "Merged", "latitude": 48.2, "longitude": 16.3, "source": "google_places"},
    }
    # The real helper (the autouse fixture replaces it), with fake tables.
    real = _REAL_OPEN_DATA_COORDINATE
    monkeypatch.setattr(stations, "_entries_by_name", lambda: entries)
    monkeypatch.setattr(
        stations, "_geonetz_coordinates", lambda: {"eva:1": (48.1, 16.1), "eva:2": (48.2, 16.2)}
    )
    monkeypatch.setattr(
        stations,
        "_wl_ogd_coordinates",
        lambda: {"60200001": (48.3, 16.3), "60200002": (48.4, 16.4)},
    )
    assert real("Rail") == stations.OpenCoordinate(48.1, 16.1, "oebb_geonetz")
    assert real("Stop") == stations.OpenCoordinate(48.3, 16.3, "wl_ogd")
    assert real("Both").dataset == "oebb_geonetz"  # type: ignore[union-attr]
    assert real("Both", prefer="wl").dataset == "wl_ogd"  # type: ignore[union-attr]
    assert real("Merged") is None
    assert real("Unknown") is None


def test_open_data_coordinate_replaces_an_implausible_wl_ogd_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # WL OGD lists DIVA 60201956 (Wassermanngasse) in the Waldviertel: the
    # WL GTFS stop answers instead, and without it there is no coordinate.
    entries: dict[str, dict[str, Any]] = {
        "Wassermanngasse": {"name": "Wassermanngasse", "wl_diva": "60201956"},
        "Halblehenweg": {"name": "Halblehenweg", "wl_diva": "60201955"},
    }
    real = _REAL_OPEN_DATA_COORDINATE
    monkeypatch.setattr(stations, "_entries_by_name", lambda: entries)
    monkeypatch.setattr(stations, "_geonetz_coordinates", lambda: {})
    monkeypatch.setattr(
        stations,
        "_wl_ogd_coordinates",
        lambda: {"60201956": (48.6945799, 15.5786106), "60201955": (48.7235788, 15.0677477)},
    )
    monkeypatch.setattr(stations, "_wl_gtfs_coordinates", lambda: {"60201956": (48.268638, 16.452806)})
    assert real("Wassermanngasse", prefer="wl") == stations.OpenCoordinate(48.268638, 16.452806, "wl_gtfs")
    assert real("Halblehenweg", prefer="wl") is None


def test_wl_gtfs_stops_map_to_divas(tmp_path: Any) -> None:
    path = tmp_path / "stops.csv"
    path.write_text(
        "stop_id,stop_name,stop_lat,stop_lon,zone_id\n"
        '"at:49:1956:0:1","Wassermanngasse","48.26863468","16.45290494","0100"\n'
        '"at:49:1956:0:2","Wassermanngasse","48.26864065","16.45270731","0100"\n'
        '"at:43:3121:0:1","Baden Josefsplatz","48.00598751","16.23376093","3045"\n'
        '"at:49:9999:0:1","Waldviertel","48.6945799","15.5786106","0100"\n'
        '"at:43:4302:0:1","No DIVA","bad","16.2","3045"\n',
        encoding="utf-8",
    )
    table = stations.load_wl_gtfs_coordinates(path)
    assert table == {
        "60201956": (48.268638, 16.452806),
        "60203121": (48.005988, 16.233761),
    }


def test_open_data_tables_load_from_the_repository() -> None:
    assert len(stations._geonetz_coordinates()) > 500
    assert len(stations._wl_ogd_coordinates()) > 1000
    gtfs = stations._wl_gtfs_coordinates()
    assert len(gtfs) > 1000
    # The three Waldviertel DIVAs of the OGD list are in Vienna in GTFS.
    for diva in ("60201954", "60201955", "60201956"):
        assert stations.is_in_vienna(*gtfs[diva])


@pytest.mark.parametrize(
    ("lang", "expected"),
    [
        ("de", ("GeoRSS-Koordinaten", "ÖBB-Infrastruktur AG", "GeoNetz", "Wiener Linien (OGD-Haltestellen)")),
        ("en", ("GeoRSS coordinates", "ÖBB-Infrastruktur AG", "GeoNetz", "Wiener Linien (OGD stops)")),
    ],
)
def test_channel_copyright_names_the_coordinate_sources(lang: str, expected: tuple[str, ...]) -> None:
    # Exactly the datasets ``open_data_coordinate`` and the Baustellen data
    # accept; the feed never carries OSM, HAFAS or Google coordinates.
    text = bf._channel_metadata(lang)["copyright"]
    for part in expected:
        assert part in text
    assert "OpenStreetMap" not in text and "HAFAS" not in text and "Google" not in text
