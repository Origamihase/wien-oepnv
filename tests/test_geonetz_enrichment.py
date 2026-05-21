"""Regression tests for the GeoNetz metadata enrichment (PR β).

The enrichment layer reads ``data/oebb_geonetz_stops.json`` — a compact
projection of the ÖBB-Infrastruktur GeoNetz dataset — and attaches
three UIC/ÖPNV identifier fields onto every station with either a
matching ``bst_id`` or, for synthetic-bst_id entries, an exact name
match. Coordinates are intentionally *not* touched by this tier (PR
#1601 governs the GeoNetz coord reconciliation; this is metadata-only).

The tests cover three axes:

1. **Loader contract** — file shape parses, ``bsts_id`` is the join
   key, missing/malformed degrades to empty without raising.
2. **Enrichment behaviour** — primary (bst_id) and secondary (name)
   joins both populate; coordinates stay untouched; idempotent on
   re-runs; ``oebb_geonetz`` source token gets appended exactly once.
3. **Live data pins** — well-known canonical values (Wien Hbf,
   Laxenburg-Biedermannsdorf) are persisted into ``stations.json`` so
   a regression doesn't silently lose the enrichment.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.update_station_directory import (
    Station,
    _enrich_with_geonetz,
    _load_geonetz_stops,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Loader contract
# ---------------------------------------------------------------------------


def test_loader_returns_empty_when_file_missing(tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    assert _load_geonetz_stops(missing) == {}


def test_loader_returns_empty_on_malformed_json(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not json {", encoding="utf-8")
    assert _load_geonetz_stops(bad) == {}


def test_loader_skips_entries_without_bsts_id(tmp_path: Path) -> None:
    f = tmp_path / "x.json"
    f.write_text(
        json.dumps(
            {
                "stops": [
                    {"bsts_id": "1", "name": "Has id"},
                    {"name": "No id — skipped"},
                    {"bsts_id": "", "name": "Empty id — skipped"},
                    "not a dict — skipped",
                ]
            }
        ),
        encoding="utf-8",
    )
    result = _load_geonetz_stops(f)
    assert list(result.keys()) == ["1"]


def test_loader_payload_shape_against_live_data() -> None:
    """The committed data/oebb_geonetz_stops.json must parse and yield
    a sensible number of Stop-Points (sanity ground-truth: 1056 in the
    GeoNetz_12-2024 dataset)."""
    p = REPO_ROOT / "data" / "oebb_geonetz_stops.json"
    assert p.exists(), f"GeoNetz stops file missing: {p}"
    result = _load_geonetz_stops(p)
    assert 1000 < len(result) < 1200, (
        f"Unexpected stop count {len(result)} — GeoNetz_12-2024 ships ~1056"
    )
    # Wien Hauptbahnhof has BSTS_ID 2393 in the reference data
    assert "2393" in result
    wien = result["2393"]
    assert wien["name"] == "Wien Hauptbahnhof"
    assert wien.get("eva_nr") == "8103000"


# ---------------------------------------------------------------------------
# Enrichment behaviour
# ---------------------------------------------------------------------------


def _make_station(bst_id: str, name: str) -> Station:
    s = Station(
        bst_id=bst_id, bst_code="X", name=name, in_vienna=False, pendler=True
    )
    s.extras = {}
    return s


def test_enrichment_primary_join_by_bst_id() -> None:
    station = _make_station("1290", "Laxenburg-Biedermannsdorf")
    lookup = {
        "1290": {
            "bsts_id": "1290",
            "name": "Laxenburg-Biedermannsdorf",
            "eva_nr": "8101122",
            "ifopt_id": "at:43:4067",
            "address": "2361 Laxenburg/Biedermannsdorf, Aspangbahnhof 1",
        }
    }
    _enrich_with_geonetz([station], lookup)
    assert station.extras["eva_nr"] == "8101122"
    assert station.extras["ifopt_id"] == "at:43:4067"
    assert "Laxenburg" in str(station.extras["address"])
    assert "oebb_geonetz" in str(station.extras["source"]).split(",")


def test_enrichment_secondary_join_by_name() -> None:
    """Stations with synthetic 900xxx-bst_id (Wien Hauptbahnhof,
    Karlsplatz etc.) get matched via exact canonical name."""
    station = _make_station("900100", "Wien Hauptbahnhof")
    lookup = {
        "2393": {
            "bsts_id": "2393",
            "name": "Wien Hauptbahnhof",
            "eva_nr": "8103000",
            "ifopt_id": "at:49:1349",
            "address": "1100 Wien, Am Hauptbahnhof 1",
        }
    }
    _enrich_with_geonetz([station], lookup)
    assert station.extras["eva_nr"] == "8103000"


def test_enrichment_skips_duplicate_names_in_secondary_join() -> None:
    """If the GeoNetz lookup happens to carry the same canonical name
    on two BSTS_IDs (rare but possible for operational sub-stations),
    the by-name fallback must not match either one — otherwise we'd
    silently attach the wrong EVA-Nr."""
    station = _make_station("999", "Knoten X")
    lookup = {
        "10": {"bsts_id": "10", "name": "Knoten X", "eva_nr": "8101111"},
        "11": {"bsts_id": "11", "name": "Knoten X", "eva_nr": "8101112"},
    }
    _enrich_with_geonetz([station], lookup)
    assert "eva_nr" not in station.extras


def test_enrichment_does_not_touch_coordinates() -> None:
    """The PR β contract explicitly says coords are NOT overwritten —
    that's the PR γ drift-detection's job. Verify by inspecting that
    ``latitude``/``longitude`` keys aren't added to extras."""
    station = _make_station("1290", "Laxenburg-Biedermannsdorf")
    station.extras["latitude"] = 48.077622  # pre-existing
    station.extras["longitude"] = 16.356588
    lookup = {
        "1290": {
            "bsts_id": "1290",
            "name": "Laxenburg-Biedermannsdorf",
            "eva_nr": "8101122",
            # GeoNetz also has lat/lon — but the enrichment ignores them
            "lat": 99.0,
            "lon": 99.0,
        }
    }
    _enrich_with_geonetz([station], lookup)
    assert station.extras["latitude"] == 48.077622
    assert station.extras["longitude"] == 16.356588


def test_enrichment_is_idempotent() -> None:
    station = _make_station("1290", "Laxenburg-Biedermannsdorf")
    lookup = {
        "1290": {
            "bsts_id": "1290",
            "name": "Laxenburg-Biedermannsdorf",
            "eva_nr": "8101122",
            "ifopt_id": "at:43:4067",
        }
    }
    _enrich_with_geonetz([station], lookup)
    first_source = station.extras["source"]
    _enrich_with_geonetz([station], lookup)
    # Re-run with identical lookup must not duplicate the source token
    assert station.extras["source"] == first_source
    assert str(station.extras["source"]).count("oebb_geonetz") == 1


def test_enrichment_preserves_existing_eva_nr() -> None:
    station = _make_station("1290", "Laxenburg-Biedermannsdorf")
    station.extras["eva_nr"] = "9999999"  # pre-existing — must NOT be overwritten
    lookup = {"1290": {"bsts_id": "1290", "name": "Laxenburg-Biedermannsdorf", "eva_nr": "8101122"}}
    _enrich_with_geonetz([station], lookup)
    assert station.extras["eva_nr"] == "9999999"


def test_enrichment_appends_to_existing_source_alphabetically() -> None:
    station = _make_station("1290", "Laxenburg-Biedermannsdorf")
    station.extras["source"] = "oebb,osm"
    lookup = {"1290": {"bsts_id": "1290", "name": "Laxenburg-Biedermannsdorf", "eva_nr": "8101122"}}
    _enrich_with_geonetz([station], lookup)
    assert station.extras["source"] == "oebb,oebb_geonetz,osm"


# ---------------------------------------------------------------------------
# Live-data pins
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def stations_payload() -> list[dict[str, Any]]:
    raw = json.loads(
        (REPO_ROOT / "data" / "stations.json").read_text(encoding="utf-8")
    )
    payload = raw["stations"]
    assert isinstance(payload, list)
    return payload


@pytest.mark.parametrize(
    "name, expected_eva",
    [
        # NB: "Wien Hauptbahnhof" itself isn't in stations.json — only its
        # Wiener-Linien sub-platforms ("Wien Hauptbahnhof (WL)" etc.,
        # source=wl, no rail eva_nr). Westbahnhof IS the closest large
        # Wien-Innenstadt ÖBB anchor that carries an eva_nr.
        ("Wien Westbahnhof", "8100003"),
        ("Laxenburg-Biedermannsdorf", "8101122"),
        ("Mistelbach Stadt", "8102007"),
        ("Himberg", "8100950"),
    ],
)
def test_live_data_has_geonetz_eva_nr(
    stations_payload: list[dict[str, Any]], name: str, expected_eva: str
) -> None:
    """Known canonical EVA values must be persisted into stations.json
    so the GeoNetz enrichment isn't silently lost on a future refresh."""
    entry = next((s for s in stations_payload if s.get("name") == name), None)
    assert entry is not None, f"{name!r} missing from stations.json"
    assert entry.get("eva_nr") == expected_eva, (
        f"{name!r} expected eva_nr={expected_eva!r}, got {entry.get('eva_nr')!r}"
    )


def test_live_data_geonetz_source_token_present(
    stations_payload: list[dict[str, Any]],
) -> None:
    """Every station carrying an eva_nr must declare the
    ``oebb_geonetz`` provenance token in its source field."""
    bad: list[str] = []
    for s in stations_payload:
        if not s.get("eva_nr"):
            continue
        src = s.get("source", "")
        tokens = {t.strip() for t in src.split(",") if t.strip()}
        if "oebb_geonetz" not in tokens:
            bad.append(s.get("name", "<unnamed>"))
    assert bad == [], (
        f"{len(bad)} stations carry eva_nr without the oebb_geonetz "
        f"source token: {bad[:5]}"
    )
