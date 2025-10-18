from __future__ import annotations

import pytest

from scripts import update_station_directory as usd


def _write_text(path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def make_station(name: str, *, in_vienna: bool = True) -> usd.Station:
    return usd.Station(bst_id=1, bst_code="X", name=name, in_vienna=in_vienna, pendler=False)


def make_stop(
    vor_id: str,
    name: str,
    *,
    municipality: str | None = None,
    short_name: str | None = None,
) -> usd.VORStop:
    return usd.VORStop(vor_id=vor_id, name=name, municipality=municipality, short_name=short_name)


def test_assign_vor_ids_exact_match() -> None:
    station = make_station("Wien Aspern Nord")
    stops = [make_stop("490091000", "Wien Aspern Nord", municipality="Wien")]
    usd._assign_vor_ids([station], stops)
    assert station.vor_id == "490091000"


def test_assign_vor_ids_prefers_matching_municipality() -> None:
    station = make_station("Korneuburg", in_vienna=False)
    stops = [
        make_stop("900500", "Korneuburg Bahnhof", municipality="Korneuburg"),
        make_stop("900501", "Korneuburg Bahnhof", municipality="Wien"),
    ]
    usd._assign_vor_ids([station], stops)
    assert station.vor_id == "900500"


def test_assign_vor_ids_ambiguous_leaves_empty() -> None:
    station = make_station("Test")
    stops = [make_stop("1", "Test"), make_stop("2", "Test")]
    usd._assign_vor_ids([station], stops)
    assert station.vor_id is None


def test_restore_existing_metadata_preserves_vor_id() -> None:
    station = make_station("Wien Mitte")
    usd._restore_existing_metadata(
        [station],
        {1: {"vor_id": "900400"}},
    )
    assert station.vor_id == "900400"


def test_restore_existing_metadata_preserves_additional_fields() -> None:
    station = make_station("Wien Mitte")
    usd._restore_existing_metadata(
        [station],
        {
            1: {
                "aliases": ["Alt Wien"],
                "_google_place_id": "abc123",
                "_lat": 48.2,
                "_lng": 16.3,
                "_types": ["train_station"],
                "_formatted_address": "Wien Mitte, 1030 Wien",
                "latitude": 48.2,
                "longitude": 16.3,
                "source": ["google_places"],
            }
        },
    )
    payload = station.as_dict()
    assert payload["aliases"] == ["Alt Wien"]
    assert payload["_google_place_id"] == "abc123"
    assert payload["_lat"] == pytest.approx(48.2)
    assert payload["_lng"] == pytest.approx(16.3)
    assert payload["latitude"] == pytest.approx(48.2)
    assert payload["longitude"] == pytest.approx(16.3)
    assert payload["source"] == ["google_places"]


def test_build_location_index_prefers_wl_coordinates(tmp_path) -> None:
    gtfs_path = tmp_path / "stops.txt"
    wl_path = tmp_path / "wl.csv"

    _write_text(
        gtfs_path,
        "stop_id,stop_name,stop_lat,stop_lon,location_type\n"
        "1,Wien Beispiel,0,0,1\n",
    )

    _write_text(
        wl_path,
        "NAME;WGS84_LAT;WGS84_LON\n"
        "Wien Beispiel;48.2;16.3\n",
    )

    locations = usd._build_location_index(gtfs_path, wl_path)
    assert locations, "expected combined locations"

    info = next(iter(locations.values()))
    assert info.latitude == 48.2
    assert info.longitude == 16.3
    assert info.sources == {"gtfs", "wl"}


def test_station_update_from_entry_merges_google_metadata() -> None:
    station = make_station("Wien Mitte")
    station.update_from_entry(
        {
            "bst_id": 1,
            "_google_place_id": "place-1",
            "_lat": 48.2082,
            "_lng": 16.3738,
            "aliases": ["Wien Mitte Station"],
            "source": ["google_places"],
            "_types": ["train_station"],
        }
    )
    payload = station.as_dict()
    assert payload["_google_place_id"] == "place-1"
    assert payload["_lat"] == pytest.approx(48.2082)
    assert payload["_lng"] == pytest.approx(16.3738)
    assert payload["latitude"] == pytest.approx(48.2082)
    assert payload["longitude"] == pytest.approx(16.3738)
    assert payload["aliases"] == ["Wien Mitte Station"]
    assert payload["source"] == ["google_places"]
