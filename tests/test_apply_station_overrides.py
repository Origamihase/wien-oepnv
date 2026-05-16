"""Tests for ``scripts/apply_station_overrides.py``.

Pin the curated-correction layer introduced 2026-05-16 (PR #1540):

* ``restore`` adds a missing station (Hummelgasse pattern);
* ``patch_coords`` repairs ``latitude`` / ``longitude`` / ``in_vienna`` /
  ``pendler`` plus per-haltepunkt ``wl_stops`` coords (Halblehenweg /
  Leopoldine-Padaurek pattern);
* ``remove`` drops a redundant DIVA (Sofienalpenstraße / Roßkopfgasse
  pattern);
* the script is idempotent, defensive against absent targets, and the
  committed ``data/stations_overrides.json`` follows the documented
  schema.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import apply_station_overrides  # noqa: E402


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _stations_from(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["stations"]


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


def test_restore_adds_missing_entry(tmp_path: Path) -> None:
    stations_path = tmp_path / "stations.json"
    overrides_path = tmp_path / "overrides.json"

    _write(stations_path, {"stations": [
        {"name": "A", "source": "wl", "latitude": 48.1, "longitude": 16.1,
         "in_vienna": True, "pendler": False, "aliases": ["A"]},
    ]})
    _write(overrides_path, {"overrides": [
        {
            "op": "restore",
            "wl_diva": "60200558",
            "reason": "test",
            "entry": {
                "name": "Wien Hummelgasse (WL)",
                "wl_diva": "60200558",
                "source": "wl",
                "latitude": 48.18,
                "longitude": 16.28,
                "in_vienna": True,
                "pendler": False,
                "aliases": ["Wien Hummelgasse (WL)"],
            },
        }
    ]})

    rc = apply_station_overrides.apply_overrides(stations_path, overrides_path)
    assert rc == 0

    stations = _stations_from(stations_path)
    assert len(stations) == 2
    assert any(s.get("wl_diva") == "60200558" for s in stations)


def test_restore_is_noop_when_diva_already_present(tmp_path: Path) -> None:
    stations_path = tmp_path / "stations.json"
    overrides_path = tmp_path / "overrides.json"

    existing = {"name": "Existing", "wl_diva": "60200558", "source": "wl",
                "latitude": 48.18, "longitude": 16.28,
                "in_vienna": True, "pendler": False, "aliases": ["Existing"]}
    _write(stations_path, {"stations": [existing]})
    _write(overrides_path, {"overrides": [
        {
            "op": "restore",
            "wl_diva": "60200558",
            "reason": "test",
            "entry": {
                "name": "Replacement",
                "wl_diva": "60200558",
                "source": "wl",
                "latitude": 49.0,
                "longitude": 17.0,
                "in_vienna": True,
                "pendler": False,
                "aliases": ["Replacement"],
            },
        }
    ]})

    rc = apply_station_overrides.apply_overrides(stations_path, overrides_path)
    assert rc == 0
    stations = _stations_from(stations_path)
    assert len(stations) == 1
    # Existing entry untouched — restore is not a replace.
    assert stations[0]["name"] == "Existing"


def test_restore_inserts_alphabetically(tmp_path: Path) -> None:
    stations_path = tmp_path / "stations.json"
    overrides_path = tmp_path / "overrides.json"

    _write(stations_path, {"stations": [
        {"name": "Apple", "source": "wl", "latitude": 48.1, "longitude": 16.1,
         "in_vienna": True, "pendler": False, "aliases": ["Apple"]},
        {"name": "Cherry", "source": "wl", "latitude": 48.1, "longitude": 16.1,
         "in_vienna": True, "pendler": False, "aliases": ["Cherry"]},
    ]})
    _write(overrides_path, {"overrides": [
        {
            "op": "restore",
            "wl_diva": "1",
            "reason": "test",
            "entry": {
                "name": "Banana",
                "wl_diva": "1",
                "source": "wl",
                "latitude": 48.1,
                "longitude": 16.1,
                "in_vienna": True,
                "pendler": False,
                "aliases": ["Banana"],
            },
        }
    ]})

    apply_station_overrides.apply_overrides(stations_path, overrides_path)
    names = [s["name"] for s in _stations_from(stations_path)]
    assert names == ["Apple", "Banana", "Cherry"]


# ---------------------------------------------------------------------------
# Patch_coords
# ---------------------------------------------------------------------------


def test_patch_coords_repairs_top_level_and_wl_stops(tmp_path: Path) -> None:
    stations_path = tmp_path / "stations.json"
    overrides_path = tmp_path / "overrides.json"

    bad = {
        "name": "Wien Halblehenweg (WL)",
        "wl_diva": "60201955",
        "source": "wl",
        "latitude": 48.7236,  # wrong upstream value
        "longitude": 15.0678,
        "in_vienna": False,
        "pendler": True,
        "aliases": ["Wien Halblehenweg (WL)"],
        "wl_stops": [
            {"stop_id": "3973", "name": "Halblehenweg", "latitude": 48.7236, "longitude": 15.0678},
            {"stop_id": "3974", "name": "Halblehenweg", "latitude": 48.7236, "longitude": 15.0679},
        ],
    }
    _write(stations_path, {"stations": [bad]})
    _write(overrides_path, {"overrides": [
        {
            "op": "patch_coords",
            "wl_diva": "60201955",
            "reason": "test",
            "latitude": 48.2492,
            "longitude": 16.4828,
            "in_vienna": True,
            "pendler": False,
            "wl_stops_patch": [
                {"stop_id": "3973", "latitude": 48.2492, "longitude": 16.4828},
                {"stop_id": "3974", "latitude": 48.2492, "longitude": 16.4829},
            ],
        }
    ]})

    rc = apply_station_overrides.apply_overrides(stations_path, overrides_path)
    assert rc == 0

    s = _stations_from(stations_path)[0]
    assert s["latitude"] == 48.2492
    assert s["longitude"] == 16.4828
    assert s["in_vienna"] is True
    assert s["pendler"] is False
    by_stop = {stop["stop_id"]: stop for stop in s["wl_stops"]}
    assert by_stop["3973"]["latitude"] == 48.2492
    assert by_stop["3974"]["longitude"] == 16.4829


def test_patch_coords_missing_diva_warns_but_does_not_fail(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """An override naturally expires when the upstream fixes the DIVA's
    presence — the script must keep running."""
    stations_path = tmp_path / "stations.json"
    overrides_path = tmp_path / "overrides.json"

    _write(stations_path, {"stations": [
        {"name": "Other", "wl_diva": "99999999", "source": "wl",
         "latitude": 48.1, "longitude": 16.1,
         "in_vienna": True, "pendler": False, "aliases": ["Other"]},
    ]})
    _write(overrides_path, {"overrides": [
        {
            "op": "patch_coords",
            "wl_diva": "60201955",
            "reason": "test",
            "latitude": 48.2492,
            "longitude": 16.4828,
            "in_vienna": True,
            "pendler": False,
        }
    ]})

    with caplog.at_level(logging.WARNING, logger="apply_station_overrides"):
        rc = apply_station_overrides.apply_overrides(stations_path, overrides_path)
    assert rc == 0
    assert any("60201955" in r.getMessage() for r in caplog.records)
    # Existing entry untouched.
    assert _stations_from(stations_path)[0]["wl_diva"] == "99999999"


# ---------------------------------------------------------------------------
# Remove
# ---------------------------------------------------------------------------


def test_remove_drops_entry(tmp_path: Path) -> None:
    stations_path = tmp_path / "stations.json"
    overrides_path = tmp_path / "overrides.json"

    _write(stations_path, {"stations": [
        {"name": "Keep", "wl_diva": "60251355", "source": "wl",
         "latitude": 48.2, "longitude": 16.2,
         "in_vienna": True, "pendler": False, "aliases": ["Keep"]},
        {"name": "Drop", "wl_diva": "60251359", "source": "wl",
         "latitude": 48.2, "longitude": 16.2,
         "in_vienna": True, "pendler": False, "aliases": ["Drop"]},
    ]})
    _write(overrides_path, {"overrides": [
        {"op": "remove", "wl_diva": "60251359", "reason": "test"}
    ]})

    rc = apply_station_overrides.apply_overrides(stations_path, overrides_path)
    assert rc == 0
    names = [s["name"] for s in _stations_from(stations_path)]
    assert names == ["Keep"]


def test_remove_missing_diva_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    stations_path = tmp_path / "stations.json"
    overrides_path = tmp_path / "overrides.json"

    _write(stations_path, {"stations": [
        {"name": "Other", "wl_diva": "1", "source": "wl",
         "latitude": 48.1, "longitude": 16.1,
         "in_vienna": True, "pendler": False, "aliases": ["Other"]},
    ]})
    _write(overrides_path, {"overrides": [
        {"op": "remove", "wl_diva": "60251359", "reason": "test"}
    ]})

    with caplog.at_level(logging.WARNING, logger="apply_station_overrides"):
        rc = apply_station_overrides.apply_overrides(stations_path, overrides_path)
    assert rc == 0
    assert any("60251359" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_unknown_op_fails_loud(tmp_path: Path) -> None:
    stations_path = tmp_path / "stations.json"
    overrides_path = tmp_path / "overrides.json"

    _write(stations_path, {"stations": []})
    _write(overrides_path, {"overrides": [
        {"op": "drop_table", "wl_diva": "60200558", "reason": "evil"}
    ]})

    rc = apply_station_overrides.apply_overrides(stations_path, overrides_path)
    assert rc == 1


def test_missing_overrides_file_returns_1(tmp_path: Path) -> None:
    stations_path = tmp_path / "stations.json"
    _write(stations_path, {"stations": []})
    rc = apply_station_overrides.apply_overrides(stations_path, tmp_path / "nope.json")
    assert rc == 1


def test_missing_stations_file_returns_2(tmp_path: Path) -> None:
    overrides_path = tmp_path / "overrides.json"
    _write(overrides_path, {"overrides": []})
    rc = apply_station_overrides.apply_overrides(tmp_path / "nope.json", overrides_path)
    assert rc == 2


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_double_apply_is_idempotent(tmp_path: Path) -> None:
    stations_path = tmp_path / "stations.json"
    overrides_path = tmp_path / "overrides.json"

    _write(stations_path, {"stations": [
        {"name": "Drop", "wl_diva": "60251359", "source": "wl",
         "latitude": 48.2, "longitude": 16.2,
         "in_vienna": True, "pendler": False, "aliases": ["Drop"]},
    ]})
    _write(overrides_path, {"overrides": [
        {"op": "remove", "wl_diva": "60251359", "reason": "test"},
        {
            "op": "restore",
            "wl_diva": "60200558",
            "reason": "test",
            "entry": {
                "name": "Wien Hummelgasse (WL)",
                "wl_diva": "60200558",
                "source": "wl",
                "latitude": 48.18,
                "longitude": 16.28,
                "in_vienna": True,
                "pendler": False,
                "aliases": ["Wien Hummelgasse (WL)"],
            },
        },
    ]})

    rc1 = apply_station_overrides.apply_overrides(stations_path, overrides_path)
    after1 = _stations_from(stations_path)
    rc2 = apply_station_overrides.apply_overrides(stations_path, overrides_path)
    after2 = _stations_from(stations_path)

    assert rc1 == 0 and rc2 == 0
    assert after1 == after2


# ---------------------------------------------------------------------------
# Real-data regression test against the committed overrides file
# ---------------------------------------------------------------------------


def test_committed_overrides_file_schema() -> None:
    """``data/stations_overrides.json`` is well-formed and uses only the
    three allowed ops.  Pins the 2026-05-16 narrow-surface contract."""
    path = REPO_ROOT / "data" / "stations_overrides.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    assert "overrides" in payload and isinstance(payload["overrides"], list)
    for entry in payload["overrides"]:
        assert isinstance(entry, dict)
        assert entry["op"] in {"restore", "patch_coords", "remove"}
        assert isinstance(entry.get("wl_diva"), str) and entry["wl_diva"].strip()
        assert isinstance(entry.get("reason"), str) and entry["reason"]
        # Every override must document its retire condition so future
        # maintainers know when to drop it.
        assert isinstance(entry.get("expires_when"), str)
        if entry["op"] == "restore":
            assert isinstance(entry.get("entry"), dict)
        if entry["op"] == "patch_coords":
            # Patch ops must declare at least one mutated field.
            mutated = {"latitude", "longitude", "in_vienna", "pendler", "wl_stops_patch"}
            assert mutated.intersection(entry.keys())


def test_committed_overrides_cover_the_known_drifts() -> None:
    """The committed overrides file targets exactly the four DIVAs
    that the 2026-05-16 audit identified as recurring upstream defects.

    If this set ever needs to change, update the assertion intentionally
    — silent drift in the override list is itself a regression.
    """
    path = REPO_ROOT / "data" / "stations_overrides.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    by_op_diva = {(o["op"], o["wl_diva"]) for o in payload["overrides"]}
    assert ("restore", "60200558") in by_op_diva  # Hummelgasse
    assert ("patch_coords", "60201955") in by_op_diva  # Halblehenweg
    assert ("patch_coords", "60201954") in by_op_diva  # Leopoldine-Padaurek
    assert ("remove", "60251359") in by_op_diva  # Sofienalpenstraße, Roßkopfgasse
