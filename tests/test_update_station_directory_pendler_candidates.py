"""Tests for the name-based pendler whitelist (data/pendler_candidates.json).

These tests cover the loader (`load_pendler_name_candidates`) and the
integration with `_annotate_station_flags`. The bst_id-based whitelist
remains the primary source; the name-based candidate list complements it
for stations whose bst_id is not yet known to the editor.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import update_station_directory as usd


def _make_station(name: str, *, bst_id: str = "999") -> usd.Station:
    return usd.Station(bst_id=bst_id, bst_code="X", name=name, in_vienna=False, pendler=False)


def test_load_pendler_name_candidates_returns_normalized_keys(tmp_path: Path) -> None:
    path = tmp_path / "pendler_candidates.json"
    path.write_text(
        json.dumps(
            {
                "candidates": [
                    {"name": "Pfaffstätten", "line": "S-Bahn Südbahn"},
                    {"name": "Münchendorf", "priority": 1},
                    {"name": "  Spillern  "},  # whitespace tolerated
                ]
            }
        ),
        encoding="utf-8",
    )

    keys = usd.load_pendler_name_candidates(path)
    assert "pfaffstatten" in keys
    assert "munchendorf" in keys
    assert "spillern" in keys


def test_load_pendler_name_candidates_includes_alternative_names(tmp_path: Path) -> None:
    """alternative_names broaden coverage when ÖBB Excel uses a different
    spelling than the canonical research name."""
    path = tmp_path / "pendler_candidates.json"
    path.write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "name": "Guntramsdorf Südbahn",
                        "alternative_names": ["Guntramsdorf"],
                    },
                    {
                        "name": "Götzendorf an der Leitha",
                        "alternative_names": ["Götzendorf"],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    keys = usd.load_pendler_name_candidates(path)
    # Both canonical and alternative spellings must produce match keys.
    assert "guntramsdorf sudbahn" in keys
    assert "guntramsdorf" in keys
    assert "gotzendorf an der leitha" in keys
    assert "gotzendorf" in keys


def test_load_pendler_name_candidates_handles_missing_file(tmp_path: Path) -> None:
    """Absent file degrades to an empty set; bst_id whitelist remains primary."""
    keys = usd.load_pendler_name_candidates(tmp_path / "absent.json")
    assert keys == set()


def test_load_pendler_name_candidates_handles_malformed_json(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("not json at all", encoding="utf-8")
    assert usd.load_pendler_name_candidates(path) == set()


def test_load_pendler_name_candidates_skips_non_dict_entries(tmp_path: Path) -> None:
    path = tmp_path / "mixed.json"
    path.write_text(
        json.dumps(
            {"candidates": ["bare string", {"name": "Achau"}, {"no_name": True}]}
        ),
        encoding="utf-8",
    )
    keys = usd.load_pendler_name_candidates(path)
    assert keys == {"achau"}


def test_annotate_marks_name_candidate_as_pendler() -> None:
    """A station NOT in the bst_id whitelist but matching a name candidate
    gets pendler=True on the next refresh — the whole point of the file."""
    station = _make_station("Pfaffstätten", bst_id="9999")
    locations: dict[str, usd.LocationInfo] = {}
    usd._annotate_station_flags(
        [station],
        pendler_ids=set(),
        locations=locations,
        pendler_name_candidates={"pfaffstatten"},
    )
    assert station.in_vienna is False
    assert station.pendler is True


def test_annotate_falls_back_to_bst_id_when_name_not_in_candidates() -> None:
    """Backward compat: a bst_id-whitelist hit alone still marks pendler=true."""
    station = _make_station("Mödling", bst_id="1377")
    usd._annotate_station_flags(
        [station],
        pendler_ids={"1377"},
        locations={},
        pendler_name_candidates=set(),
    )
    assert station.pendler is True


def test_annotate_in_vienna_wins_over_name_candidate() -> None:
    """Vienna stations always win — the mutual-exclusivity invariant from
    PR #1192 must hold even when the name accidentally hits a candidate."""
    station = _make_station("Wien Westbahnhof", bst_id="2511")
    locations = {
        "wien westbahnhof": usd.LocationInfo(
            latitude=48.196654, longitude=16.337652, sources={"oebb"}
        )
    }
    usd._annotate_station_flags(
        [station],
        pendler_ids=set(),
        locations=locations,
        pendler_name_candidates={"wien westbahnhof"},  # mistakenly added
    )
    assert station.in_vienna is True
    assert station.pendler is False, (
        "in_vienna must win over both bst_id and name-based pendler markers"
    )


def test_pendler_candidates_file_validates_against_schema() -> None:
    """Lock the on-disk candidate file against its JSON Schema."""
    jsonschema = pytest.importorskip("jsonschema")
    repo_root = Path(__file__).resolve().parent.parent
    schema_path = repo_root / "docs" / "schema" / "pendler_candidates.schema.json"
    candidates_path = repo_root / "data" / "pendler_candidates.json"

    with schema_path.open(encoding="utf-8") as handle:
        schema = json.load(handle)
    with candidates_path.open(encoding="utf-8") as handle:
        data = json.load(handle)

    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda exc: exc.path)
    assert not errors, "\n".join(
        f"{'.'.join(str(p) for p in err.absolute_path)}: {err.message}"
        for err in errors
    )


def test_pendler_candidates_top_12_present() -> None:
    """Live-data pin: the 12 critical pendler stations from the
    2026-05 stations-coverage research must be on the candidate list."""
    repo_root = Path(__file__).resolve().parent.parent
    candidates_path = repo_root / "data" / "pendler_candidates.json"
    with candidates_path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    names = {c["name"] for c in data["candidates"]}

    top_12 = {
        "Pfaffstätten", "Gumpoldskirchen", "Guntramsdorf Südbahn",
        "Hennersdorf", "Achau", "Münchendorf",
        "Gramatneusiedl", "Götzendorf an der Leitha", "Himberg bei Wien",
        "Felixdorf", "Sollenau", "Traiskirchen Aspangbahn",
    }
    missing = top_12 - names
    assert not missing, f"Top-12 pendler stations missing from candidates: {missing}"


def test_load_vor_locations_picks_up_coords(tmp_path: Path) -> None:
    """VOR CSV must populate the location index so pendler stations missing
    from the GTFS snapshot still get coordinates assigned."""
    vor_path = tmp_path / "vor-haltestellen.csv"
    vor_path.write_text(
        "StopPointId;StopPointName;Latitude;Longitude\n"
        "490009999;Pfaffstätten;48.0185;16.2596\n"
        "490009998;Gumpoldskirchen;48.0416;16.2851\n",
        encoding="utf-8",
    )

    locations = usd._load_vor_locations(vor_path)
    keys = set(locations.keys())
    assert "pfaffstatten" in keys
    assert "gumpoldskirchen" in keys
    pfaff = locations[next(k for k in keys if k == "pfaffstatten")]
    assert pfaff.latitude == pytest.approx(48.0185)
    assert pfaff.longitude == pytest.approx(16.2596)
    assert "vor" in pfaff.sources


def test_build_location_index_merges_three_sources(tmp_path: Path) -> None:
    """When GTFS, WL and VOR all provide a name, GTFS/WL take precedence —
    VOR fills only the gaps. This preserves the higher-precision authoritative
    sources while still resolving the long tail of pendler-only stations."""
    gtfs = tmp_path / "stops.txt"
    gtfs.write_text(
        "stop_id,stop_code,stop_name,stop_lat,stop_lon,location_type,parent_station,platform_code\n"
        "490104000,Nw,Wien Praterstern,48.218767,16.392171,1,,\n",
        encoding="utf-8",
    )
    wl = tmp_path / "wl.csv"
    wl.write_text(
        '"HALTEPUNKT_ID";"HALTESTELLEN_ID";"STOP_ID";"NAME";"Municipality";"RBL_NUMMER";"WGS84_LAT";"WGS84_LON"\n'
        '"1";"1001";"60201076";"Karlsplatz";"Wien";"60201076";"48.198680";"16.369450"\n',
        encoding="utf-8",
    )
    vor = tmp_path / "vor.csv"
    vor.write_text(
        # Pfaffstätten is only in VOR — GTFS/WL don't have it (the typical
        # pendler-station gap).
        "StopPointId;StopPointName;Latitude;Longitude\n"
        "490009999;Pfaffstätten;48.0185;16.2596\n"
        # Wien Praterstern is also in VOR but with slightly different coords —
        # GTFS must win (verifies precedence).
        "490104000;Wien Praterstern;48.220000;16.395000\n",
        encoding="utf-8",
    )

    index = usd._build_location_index(gtfs, wl, vor_path=vor)

    # Pfaffstätten resolves only via VOR.
    assert "pfaffstatten" in index
    assert index["pfaffstatten"].latitude == pytest.approx(48.0185)

    # Praterstern keeps the GTFS value (precedence).
    praterstern = index["wien praterstern"]
    assert praterstern.latitude == pytest.approx(48.218767)
    assert "gtfs" in praterstern.sources
    assert "vor" not in praterstern.sources, (
        "GTFS-precedence broken: VOR overwrote a GTFS-resolved location"
    )
