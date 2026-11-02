"""Capped CSV coordinate and VOR stop loaders of the station-directory updater.

Moved verbatim out of ``scripts/update_station_directory.py``, which
re-exports every name it or its tests use.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from pathlib import Path
from collections.abc import Iterable, Mapping

from src.station_directory.model import (
    LocationInfo,
    VORStop,
    _coerce_float_value,
    _harmonize_station_name,
    _normalize_location_keys,
    _store_location,
    _wl_diva_key,
)
from src.utils.env import _path_fingerprint
from src.utils.files import read_capped_text

logger = logging.getLogger("update_station_directory")


# Security cap against wide-but-flat CSV size-bomb attacks. Routes every
# operator-controlled CSV file (GTFS stops, WL haltepunkte, VOR
# haltestellen) through ``read_capped_text`` -> ``io.StringIO`` ->
# ``csv.DictReader`` so a planted unbounded CSV (single huge line, no
# newlines) cannot buffer GiB of payload via ``handle.readline()`` and
# propagate ``MemoryError`` (``BaseException`` subclass NOT caught by
# ``except (OSError, csv.Error)``) past the cron orchestrator.
# Same 50 MiB ceiling as ``MAX_JSON_FILE_BYTES`` — comfortably above any
# legitimate transit-network CSV (Austria-wide GTFS dumps stay well
# under 50 MiB) and well below the runner's 1 GiB cgroup limit.
MAX_CSV_LOCATIONS_BYTES = 50 * 1024 * 1024


def _load_gtfs_locations(path: Path) -> dict[str, LocationInfo]:
    locations: dict[str, LocationInfo] = {}
    if not path.exists():
        logger.warning(
            "GTFS stops file not found: [path-sha256=%s]",
            _path_fingerprint(path),
        )
        return locations
    # Security: route through ``read_capped_text`` to bound the
    # ``csv.DictReader`` -> ``readline()`` allocation against planted
    # unbounded CSVs. See ``MAX_CSV_LOCATIONS_BYTES`` for the threat
    # model. ``read_capped_text`` returns ``None`` on missing /
    # oversized / decode-error so subsequent code receives an empty
    # mapping rather than crashing the cron pipeline.
    content = read_capped_text(
        path,
        MAX_CSV_LOCATIONS_BYTES,
        encoding="utf-8",
        label="GTFS stops",
        logger=logger,
    )
    if content is None:
        return locations
    try:
        reader = csv.DictReader(io.StringIO(content))
        for row in reader:
            stop_name = row.get("stop_name")
            if not stop_name:
                continue
            stop_name = _harmonize_station_name(stop_name)
            lat = _coerce_float_value(row.get("stop_lat"))
            lon = _coerce_float_value(row.get("stop_lon"))
            if lat is None or lon is None:
                continue
            location_type = (row.get("location_type") or "").strip()
            is_station = location_type == "1"
            for key in _normalize_location_keys(stop_name):
                if not key:
                    continue
                if is_station or key not in locations:
                    _store_location(locations, key, lat, lon, source="gtfs")
    except csv.Error as exc:
        logger.warning(
            "Could not parse GTFS stops file [path-sha256=%s]: %s",
            _path_fingerprint(path),
            exc,
        )
    else:
        if locations:
            logger.info("Loaded %d GTFS stop coordinates", len(locations))
    return locations


def _load_wienerlinien_locations(path: Path) -> dict[str, LocationInfo]:
    locations: dict[str, LocationInfo] = {}
    if not path.exists():
        logger.warning(
            "Wiener Linien haltepunkte file not found: [path-sha256=%s]",
            _path_fingerprint(path),
        )
        return locations
    # Security: see _load_gtfs_locations for the canonical CSV
    # size-bomb defence shape (read_capped_text + io.StringIO).
    content = read_capped_text(
        path,
        MAX_CSV_LOCATIONS_BYTES,
        encoding="utf-8",
        label="WL haltepunkte",
        logger=logger,
    )
    if content is None:
        return locations
    try:
        reader = csv.DictReader(io.StringIO(content), delimiter=";")
        for row in reader:
            # The Wiener Linien OGD CSV schema migrated (PR #1442): the
            # legacy data.wien.gv.at proxy export keyed on NAME /
            # WGS84_LAT / WGS84_LON; the canonical wienerlinien.at
            # OGD-Echtzeit export that replaced it renames those to
            # StopText / Latitude / Longitude and exposes the DIVA. Read
            # the new names first and fall back to the legacy ones so the
            # loader survives either upstream shape (mirrors the fuzzy-key
            # resilience in scripts/update_wl_stations.py).
            name = row.get("StopText") or row.get("NAME")
            if name:
                name = _harmonize_station_name(name)
            lat = _coerce_float_value(row.get("Latitude") or row.get("WGS84_LAT"))
            lon = _coerce_float_value(row.get("Longitude") or row.get("WGS84_LON"))
            if lat is None or lon is None:
                continue
            diva = (row.get("DIVA") or "").strip()
            if diva:
                _store_location(locations, _wl_diva_key(diva), lat, lon, source="wl")
            if not name:
                continue
            for key in _normalize_location_keys(name):
                if not key:
                    continue
                _store_location(locations, key, lat, lon, source="wl")
    except csv.Error as exc:
        logger.warning(
            "Could not parse Wiener Linien haltepunkte file [path-sha256=%s]: %s",
            _path_fingerprint(path),
            exc,
        )
    else:
        if locations:
            logger.info("Loaded %d Wiener Linien coordinates", len(locations))
    return locations


def _load_vor_locations(path: Path) -> dict[str, LocationInfo]:
    """Load name → (lat, lon) from the VOR haltestellen CSV.

    Provides a third coordinate source after GTFS and WL haltepunkte.
    The VOR CSV uses ``StopPointName;Latitude;Longitude`` columns.
    """
    locations: dict[str, LocationInfo] = {}
    if not path.exists():
        logger.warning(
            "VOR stops file not found: [path-sha256=%s]",
            _path_fingerprint(path),
        )
        return locations
    # Security: see _load_gtfs_locations for the canonical CSV
    # size-bomb defence shape (read_capped_text + io.StringIO).
    content = read_capped_text(
        path,
        MAX_CSV_LOCATIONS_BYTES,
        encoding="utf-8-sig",
        label="VOR stops",
        logger=logger,
    )
    if content is None:
        return locations
    try:
        sample = content[:4096]
        delimiter = _detect_csv_delimiter(sample)
        reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
        for row in reader:
            name = row.get("StopPointName") or row.get("Name") or row.get("StopName")
            if name:
                name = _harmonize_station_name(name)
            lat = _coerce_float_value(row.get("Latitude") or row.get("WGS84_LAT"))
            lon = _coerce_float_value(row.get("Longitude") or row.get("WGS84_LON"))
            if not name or lat is None or lon is None:
                continue
            for key in _normalize_location_keys(name):
                if not key:
                    continue
                _store_location(locations, key, lat, lon, source="vor")
    except csv.Error as exc:
        logger.warning(
            "Could not parse VOR stops file [path-sha256=%s]: %s",
            _path_fingerprint(path),
            exc,
        )
    else:
        if locations:
            logger.info("Loaded %d VOR coordinates", len(locations))
    return locations


def _build_location_index(
    gtfs_path: Path | None,
    wl_path: Path | None,
    vor_path: Path | None = None,
) -> dict[str, LocationInfo]:
    # Since 2026-10-17 the pinned VOR stop list and the GTFS stub carry no
    # coordinates: they came from the AnachB webapp without an open licence,
    # and Michael decided to drop them (NOTICE.md, "VOR-Haltestellendaten").
    # Both loaders skip rows without coordinates, so the index is fed by the
    # WL OGD stops only; a file that regains coordinates would feed it again,
    # which tests/test_notice_data_coverage.py guards against.
    locations: dict[str, LocationInfo] = {}
    if gtfs_path:
        locations.update(_load_gtfs_locations(gtfs_path))
    if wl_path:
        wl_locations = _load_wienerlinien_locations(wl_path)
        for key, value in wl_locations.items():
            _store_location(locations, key, value.latitude, value.longitude, source="wl")
    if vor_path:
        vor_locations = _load_vor_locations(vor_path)
        for key, value in vor_locations.items():
            # GTFS/WL already populated entries take precedence (more authoritative
            # for transit-platform coords); VOR fills gaps for stations that are
            # neither in the local GTFS snapshot nor in the WL haltepunkte CSV.
            if key not in locations:
                _store_location(locations, key, value.latitude, value.longitude, source="vor")
    return locations


def _detect_csv_delimiter(sample: str) -> str:
    semicolons = sample.count(";")
    commas = sample.count(",")
    if semicolons >= commas and semicolons:
        return ";"
    if commas:
        return ","
    return ";"


def _normalize_csv_key(value: str | None) -> str:
    if value is None:
        return ""
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


class _NormalizedCSVRow:
    def __init__(self, row: Mapping[str, str | None]):
        self._row = row
        self._map = {_normalize_csv_key(key): key for key in row if key}

    def get(self, *candidates: str) -> str:
        for candidate in candidates:
            key = self._map.get(_normalize_csv_key(candidate))
            if key is None:
                continue
            value = self._row.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return ""


def _iter_vor_rows(path: Path) -> Iterable[_NormalizedCSVRow]:
    # Security: see _load_gtfs_locations / MAX_CSV_LOCATIONS_BYTES for
    # the canonical CSV size-bomb defence shape (read_capped_text +
    # io.StringIO). FileNotFoundError is propagated so the caller's
    # legacy "file not found" log path remains intact.
    if not path.exists():
        raise FileNotFoundError(str(path))
    content = read_capped_text(
        path,
        MAX_CSV_LOCATIONS_BYTES,
        encoding="utf-8-sig",
        label="VOR stops",
        logger=logger,
    )
    if content is None:
        return
    sample = content[:4096]
    delimiter = _detect_csv_delimiter(sample)
    reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
    for row in reader:
        yield _NormalizedCSVRow({key or "": value for key, value in row.items()})


def load_vor_stops(path: Path) -> list[VORStop]:
    try:
        rows = list(_iter_vor_rows(path))
    except FileNotFoundError:
        logger.info(
            "VOR stops file not found: [path-sha256=%s]",
            _path_fingerprint(path),
        )
        return []
    except csv.Error as exc:
        logger.warning(
            "Could not parse VOR stops file [path-sha256=%s]: %s",
            _path_fingerprint(path),
            exc,
        )
        return []

    stops: dict[str, VORStop] = {}
    for row in rows:
        vor_id = row.get(
            "StopPointId",
            "StopID",
            "Stop_Id",
            "StopPoint",
            "ID",
        )
        if not vor_id:
            continue
        name = row.get("StopPointName", "Name", "StopName", "Bezeichnung")
        if not name:
            continue
        municipality = row.get("Municipality", "Gemeinde", "City", "Ort") or None
        short_name = row.get("StopPointShortName", "ShortName", "Kurzname") or None
        stops[vor_id] = VORStop(
            vor_id=vor_id,
            name=name,
            municipality=municipality,
            short_name=short_name,
        )

    if not stops:
        logger.info(
            "No VOR stops extracted from [path-sha256=%s]",
            _path_fingerprint(path),
        )
    else:
        logger.info(
            "Loaded %d VOR stops from [path-sha256=%s]",
            len(stops),
            _path_fingerprint(path),
        )
    return list(stops.values())
