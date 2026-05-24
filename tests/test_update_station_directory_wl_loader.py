"""Coverage for ``_load_wienerlinien_locations`` (the WL OGD coordinate tier).

Regression context: the loader read the pre-migration ``NAME`` /
``WGS84_LAT`` / ``WGS84_LON`` columns, but the canonical wienerlinien.at
OGD-Echtzeit CSV that replaced the legacy data.wien.gv.at proxy renamed
those to ``StopText`` / ``Latitude`` / ``Longitude`` and exposes the
``DIVA``. The mismatch made the loader silently load ZERO of ~5123 rows,
so WL stations that should have resolved from the free on-disk snapshot
fell through to the metered Google Places tier.

These tests pin: the current schema loads (name keys + the authoritative
DIVA key), the legacy schema still loads via fallback, and the pinned
repository snapshot yields coordinates (the direct regression guard).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import update_station_directory as usd


def test_loads_current_ogd_schema(tmp_path: Path) -> None:
    """The post-migration ``StopText`` / ``Latitude`` / ``Longitude`` /
    ``DIVA`` schema must load — both name keys and the DIVA key."""
    csv_path = tmp_path / "haltepunkte.csv"
    csv_path.write_text(
        "StopID;DIVA;StopText;Municipality;MunicipalityID;Longitude;Latitude\n"
        "2001;60200506;Herrengasse;Wien;49000001;16.3658;48.2095\n"
        "2002;60200506;Herrengasse;Wien;49000001;16.3660;48.2096\n"
        "3001;60201430;Volkstheater;Wien;49000001;16.3580;48.2051\n",
        encoding="utf-8",
    )

    locations = usd._load_wienerlinien_locations(csv_path)

    assert locations, "loader must not be empty for the current OGD schema"
    # DIVA key resolves to the first platform coordinate (representative
    # point for the stop — _store_location keeps the first write).
    diva_info = locations[usd._wl_diva_key("60200506")]
    assert diva_info.latitude == pytest.approx(48.2095)
    assert diva_info.longitude == pytest.approx(16.3658)
    assert "wl" in diva_info.sources
    # Name keys keep working alongside the DIVA key.
    assert "herrengasse" in locations
    assert "volkstheater" in locations


def test_legacy_schema_still_loads(tmp_path: Path) -> None:
    """The pre-migration ``NAME`` / ``WGS84_LAT`` / ``WGS84_LON`` columns
    must still resolve via the legacy fallback."""
    csv_path = tmp_path / "legacy.csv"
    csv_path.write_text(
        "NAME;WGS84_LAT;WGS84_LON\n"
        "Stephansplatz;48.2081;16.3716\n",
        encoding="utf-8",
    )

    locations = usd._load_wienerlinien_locations(csv_path)

    assert "stephansplatz" in locations
    info = locations["stephansplatz"]
    assert info.latitude == pytest.approx(48.2081)
    assert info.longitude == pytest.approx(16.3716)


def test_rows_without_coordinates_are_skipped(tmp_path: Path) -> None:
    """A row missing usable coordinates contributes nothing (no crash)."""
    csv_path = tmp_path / "partial.csv"
    csv_path.write_text(
        "StopID;DIVA;StopText;Longitude;Latitude\n"
        "1;60200001;NoCoords;;\n"
        "2;60200002;HasCoords;16.40;48.20\n",
        encoding="utf-8",
    )

    locations = usd._load_wienerlinien_locations(csv_path)

    assert usd._wl_diva_key("60200001") not in locations
    assert usd._wl_diva_key("60200002") in locations


def test_pinned_repository_snapshot_yields_coordinates() -> None:
    """Regression guard for the dead-column bug: the pinned WL OGD
    snapshot must produce coordinates (it loaded ZERO before the fix)."""
    locations = usd._load_wienerlinien_locations(usd.DEFAULT_WL_HALTEPUNKTE_PATH)

    assert len(locations) > 1000
    # A known WL DIVA (Herrengasse) must resolve via the authoritative key.
    assert usd._wl_diva_key("60200506") in locations
