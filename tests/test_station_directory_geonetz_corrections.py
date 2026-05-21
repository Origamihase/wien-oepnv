"""Regression guards for the GeoNetz-vs-hand-curated reconciliation
(2026-05-21 audit follow-up to PRs #1224 and #1230).

Three things go wrong easily in hand-curated geo data:

1. **Wrong rail-station coordinates** — picking the town centroid or
   an OSM bus-stop instead of the actual Bahnhof. PR #1224 introduced
   exactly this for three NÖ pendler stops; the audit against the
   official ÖBB-Infrastruktur GeoNetz dataset (GeoNetz_12-2024) showed
   one of them (Laxenburg-Biedermannsdorf) was ~10 km off — the hand
   coord landed near Möllersdorf, not the Bahnhof between Laxenburg
   and Biedermannsdorf.

2. **Phantom-station resolution loops** — keeping operatively closed
   stations in the pendler-candidates whitelist forces the VOR
   resolver to scan town-namesake bus stops every run. PRs #1207-#1209
   added six successive bus-stop-suffix filters trying to suppress
   Weigelsdorf bus stops before realising the station itself was
   decommissioned on 2023-07-01 with the Pottendorfer-Linie
   modernization (new Bahnhof Ebreichsdorf replaced it 2023-09-04).

3. **Silent re-introduction by future maintainers** — a future
   pendler_candidates.json edit that re-adds Weigelsdorf or a future
   data refresh that rolls back the GeoNetz coordinates would un-fix
   the audit. Pin both axes here so the regression is caught at CI
   time, not on the next cron tick.
"""
from __future__ import annotations

import csv
import io
import json
import math
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _haversine_m(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    """Great-circle distance in metres between two WGS84 points."""
    radius = 6_371_000
    phi_a = math.radians(a_lat)
    phi_b = math.radians(b_lat)
    dphi = math.radians(b_lat - a_lat)
    dlam = math.radians(b_lon - a_lon)
    arc = math.sin(dphi / 2) ** 2 + math.cos(phi_a) * math.cos(phi_b) * math.sin(dlam / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(arc))


@pytest.fixture(scope="module")
def stations() -> list[dict]:
    return json.loads((REPO_ROOT / "data" / "stations.json").read_text(encoding="utf-8"))["stations"]


@pytest.fixture(scope="module")
def pendler_candidates() -> dict:
    return json.loads((REPO_ROOT / "data" / "pendler_candidates.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def gtfs_stops() -> list[dict[str, str]]:
    raw = (REPO_ROOT / "data" / "gtfs" / "stops.txt").read_text(encoding="utf-8")
    return list(csv.DictReader(io.StringIO(raw)))


# ---------------------------------------------------------------------------
# 1. GeoNetz coordinate corrections — coords must NOT drift back into the
#    PR #1224 hand-curated positions. We pin a small tolerance around the
#    canonical GeoNetz values so future fine-tuning (e.g. moving from
#    platform centroid to entrance node) stays acceptable, but a full
#    revert would fail.
# ---------------------------------------------------------------------------

_GEONETZ_CANONICAL: dict[str, tuple[float, float]] = {
    # values from data.oebb.at / mobilitydata.gv.at GeoNetz_12-2024 (fahrplan 2025)
    "Himberg":                    (48.081383, 16.445482),
    "Laxenburg-Biedermannsdorf":  (48.077622, 16.356588),
    "Mistelbach Stadt":           (48.570217, 16.568812),
}

# PR #1224 hand-curated values that were wrong — must NOT re-appear.
_PR1224_WRONG: dict[str, tuple[float, float]] = {
    "Himberg":                    (48.0828, 16.4392),
    "Laxenburg-Biedermannsdorf":  (47.9893, 16.3640),   # ~10 km off (Möllersdorf-area)
    "Mistelbach Stadt":           (48.5754, 16.5821),
}


@pytest.mark.parametrize("name, canonical", list(_GEONETZ_CANONICAL.items()))
def test_pendler_coords_near_geonetz_canonical(stations, name: str, canonical: tuple[float, float]) -> None:
    """The three pendler stations whose coords PR #1224 hand-curated must
    sit within 200 m of the official ÖBB GeoNetz value."""
    entry = next((s for s in stations if s.get("name") == name), None)
    assert entry is not None, f"{name!r} missing from stations.json"
    lat, lon = entry.get("latitude"), entry.get("longitude")
    assert lat is not None and lon is not None, f"{name!r} has no coordinates"
    drift = _haversine_m(lat, lon, canonical[0], canonical[1])
    assert drift <= 200, (
        f"{name!r} coordinates drifted {drift:.0f} m from the GeoNetz canonical "
        f"{canonical} → ({lat}, {lon}). The hand-curated PR #1224 value was wrong; "
        f"do not revert."
    )


@pytest.mark.parametrize("name, wrong", list(_PR1224_WRONG.items()))
def test_pendler_coords_not_pr1224_revert(stations, name: str, wrong: tuple[float, float]) -> None:
    """Anti-revert: the literal PR #1224 hand-curated coordinates must
    never come back. Trip wire against accidental rollback."""
    entry = next((s for s in stations if s.get("name") == name), None)
    assert entry is not None
    assert (entry["latitude"], entry["longitude"]) != wrong, (
        f"{name!r} reverted to PR #1224 hand-curated coordinates {wrong} — "
        f"these were verified wrong against ÖBB GeoNetz."
    )


# ---------------------------------------------------------------------------
# 2. Weigelsdorf tombstone — operatively closed on 2023-07-01, must NOT
#    be in any active dataset.
# ---------------------------------------------------------------------------


def test_weigelsdorf_not_in_stations_json(stations) -> None:
    """Weigelsdorf is operatively closed since 2023-07-01 (Pottendorfer
    Linie modernization, new Bahnhof Ebreichsdorf is the replacement)."""
    hits = [s for s in stations if s.get("name") == "Weigelsdorf"]
    assert hits == [], (
        "Weigelsdorf was decommissioned on 2023-07-01. Re-adding it makes "
        "the VOR resolver chase phantom bus-stops on every run "
        "(see Whack-a-Mole saga in PRs #1207-#1209)."
    )


def test_weigelsdorf_not_in_pendler_candidates(pendler_candidates) -> None:
    """The pendler-whitelist must not anchor a decommissioned station."""
    hits = [c for c in pendler_candidates["candidates"] if c.get("name") == "Weigelsdorf"]
    assert hits == []


def test_weigelsdorf_not_in_gtfs_stops(gtfs_stops) -> None:
    """The GTFS stops mirror must not carry the phantom station either."""
    hits = [r for r in gtfs_stops if r.get("stop_name") == "Weigelsdorf"]
    assert hits == [], (
        "Weigelsdorf row leaked into gtfs/stops.txt — re-derive from the "
        "stations.json source-of-truth."
    )
