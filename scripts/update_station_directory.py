#!/usr/bin/env python3
"""Download and parse the ÖBB station directory Excel file.

The script exports a simplified JSON mapping (``bst_id``, ``bst_code``, ``name``,
``in_vienna`` and ``pendler``) that is used throughout the project. Station names
are harmonized with the previous export, geodata is used to flag Vienna
locations and commuter belt entries, and stations outside this area are omitted.
The data is obtained from the official ÖBB Open-Data portal.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import subprocess
import sys
import unicodedata
from copy import deepcopy
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Callable, Iterable, Mapping, MutableMapping, Sequence

import openpyxl
import requests

DEFAULT_SOURCE_URL = (
    "https://data.oebb.at/dam/jcr:fce22daf-0dd8-4a15-80b4-dbca6e80ce38/"
    "Verzeichnis%20der%20Verkehrsstationen.xlsx"
)
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:  # pragma: no cover - convenience for module execution
    from src.utils.files import atomic_write
    from src.utils.http import fetch_content_safe, session_with_retries
    from src.utils.stations import is_in_vienna as _is_point_in_vienna
except ModuleNotFoundError:  # pragma: no cover - fallback when installed as package
    from utils.files import atomic_write  # type: ignore
    from utils.http import fetch_content_safe, session_with_retries  # type: ignore
    from utils.stations import is_in_vienna as _is_point_in_vienna  # type: ignore

try:  # pragma: no cover - convenience for module execution
    from src.places.client import (
        DEFAULT_INCLUDED_TYPES,
        GooglePlacesClient,
        GooglePlacesConfig,
        GooglePlacesError,
        GooglePlacesPermissionError,
        GooglePlacesTileError,
        Place,
        get_places_api_key,
    )
    from src.places.diagnostics import permission_hint
    from src.places.merge import BoundingBox, MergeConfig, merge_places
    from src.places.tiling import Tile, iter_tiles, load_tiles_from_env, load_tiles_from_file
    from src.utils.env import load_default_env_files
except ModuleNotFoundError:  # pragma: no cover - fallback when installed as package
    from places.client import (  # type: ignore
        DEFAULT_INCLUDED_TYPES,
        GooglePlacesClient,
        GooglePlacesConfig,
        GooglePlacesError,
        GooglePlacesPermissionError,
        GooglePlacesTileError,
        Place,
        get_places_api_key,
    )
    from places.diagnostics import permission_hint  # type: ignore
    from places.merge import BoundingBox, MergeConfig, merge_places  # type: ignore
    from places.tiling import Tile, iter_tiles, load_tiles_from_env, load_tiles_from_file  # type: ignore
    from utils.env import load_default_env_files  # type: ignore

DEFAULT_OUTPUT_PATH = _ROOT / "data" / "stations.json"
DEFAULT_PENDLER_PATH = _ROOT / "data" / "pendler_bst_ids.json"
DEFAULT_GTFS_STOPS_PATH = _ROOT / "data" / "gtfs" / "stops.txt"
DEFAULT_WL_HALTEPUNKTE_PATH = _ROOT / "data" / "wienerlinien-ogd-haltepunkte.csv"
DEFAULT_VOR_STOPS_PATH = _ROOT / "data" / "vor-haltestellen.csv"
REQUEST_TIMEOUT = 30  # seconds
USER_AGENT = (
    "wien-oepnv station updater "
    "(https://github.com/Origamihase/wien-oepnv)"
)

HEADER_VARIANTS: dict[str, set[str]] = {
    "name": {"verkehrsstation"},
    "bst_code": {"bstcode"},
    "bst_id": {"bstid"},
}

logger = logging.getLogger("update_station_directory")


def _empty_args() -> tuple[str, ...]:
    return ()


@dataclass(frozen=True)
class CacheRefreshTarget:
    label: str
    script_candidates: tuple[str, ...]
    optional: bool = False
    extra_args_factory: Callable[[], Sequence[str]] = _empty_args
    availability_check: Callable[[], bool] | None = None


def _has_google_places_credentials() -> bool:
    return bool(os.getenv("GOOGLE_ACCESS_ID") or os.getenv("GOOGLE_MAPS_API_KEY"))


def _google_cache_args() -> tuple[str, ...]:
    return ("--write",)


_CACHE_REFRESH_TARGETS: tuple[CacheRefreshTarget, ...] = (
    CacheRefreshTarget("ÖBB", ("update_oebb_cache.py",)),
    CacheRefreshTarget("VOR", ("update_vor_cache.py",)),
    CacheRefreshTarget("Wiener Linien", ("update_wl_cache.py",)),
    CacheRefreshTarget(
        "Google Places",
        ("update_google_cache.py", "fetch_google_places_stations.py"),
        optional=True,
        extra_args_factory=_google_cache_args,
        availability_check=_has_google_places_credentials,
    ),
)


def _refresh_provider_caches(*, script_dir: Path | None = None) -> None:
    base_dir = script_dir or Path(__file__).resolve().parent
    for target in _CACHE_REFRESH_TARGETS:
        if target.availability_check and not target.availability_check():
            logger.info(
                "Skipping %s cache refresh (credentials not available)", target.label
            )
            continue

        command: list[str] | None = None
        script_path: Path | None = None
        for candidate in target.script_candidates:
            candidate_path = base_dir / candidate
            if candidate_path.exists():
                extra_args = list(target.extra_args_factory())
                command = [sys.executable, str(candidate_path), *extra_args]
                script_path = candidate_path
                break

        if command is None or script_path is None:
            message = (
                "No cache refresh script found for %s; skipping"
                if target.optional
                else "Cache refresh script missing for %s"
            )
            log_method = logger.debug if target.optional else logger.warning
            log_method(message, target.label)
            continue

        logger.info("Refreshing %s cache via %s", target.label, script_path.name)
        try:
            # Enforce a 5-minute timeout to prevent indefinite hangs (DoS protection)
            result = subprocess.run(command, check=False, timeout=300)
        except subprocess.TimeoutExpired:
            logger.warning(
                "%s cache refresh timed out after 300s; continuing", target.label
            )
            continue
        except OSError as exc:  # pragma: no cover - execution environment issues
            logger.warning(
                "Failed to execute %s cache refresh (%s); continuing", target.label, exc
            )
            continue

        if result.returncode != 0:
            logger.warning(
                "%s cache refresh exited with code %s; continuing",
                target.label,
                result.returncode,
            )
        else:
            logger.info("%s cache refresh completed", target.label)


@dataclass
class Station:
    """Representation of a single station entry."""

    bst_id: int
    bst_code: str
    name: str
    in_vienna: bool = False
    pendler: bool = False
    vor_id: str | None = None
    extras: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "bst_id": self.bst_id,
            "bst_code": self.bst_code,
            "name": self.name,
            "in_vienna": self.in_vienna,
            "pendler": self.pendler,
        }
        if self.vor_id:
            payload["vor_id"] = self.vor_id

        for key, value in self.extras.items():
            if key in {"bst_id", "bst_code", "name", "in_vienna", "pendler", "vor_id"}:
                continue
            payload[key] = value
        return payload

    def update_from_entry(self, entry: Mapping[str, object]) -> None:
        base_keys = {"bst_id", "bst_code", "name", "in_vienna", "pendler", "vor_id"}
        for key, value in entry.items():
            if key == "vor_id":
                if self.vor_id is None and isinstance(value, str) and value.strip():
                    self.vor_id = value.strip()
                continue
            if key in base_keys:
                continue
            self.extras[key] = deepcopy(value)

        lat = entry.get("_lat")
        lng = entry.get("_lng")
        if isinstance(lat, (int, float)):
            latitude = float(lat)
            self.extras["_lat"] = latitude
            self.extras["latitude"] = latitude
        if isinstance(lng, (int, float)):
            longitude = float(lng)
            self.extras["_lng"] = longitude
            self.extras["longitude"] = longitude


@dataclass
class VORStop:
    """Minimal representation of a VOR stop for ID matching."""

    vor_id: str
    name: str
    municipality: str | None = None
    short_name: str | None = None


@dataclass
class LocationInfo:
    """Coordinates collected from auxiliary data sources."""

    latitude: float
    longitude: float
    sources: set[str]

    def add_source(self, source: str) -> None:
        self.sources.add(source)


def _strip_accents(value: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch))


def _normalize_location_keys(name: str | None) -> list[str]:
    if not name:
        return []

    candidates = [name]
    without_parens = re.sub(r"\s*\([^)]*\)\s*", " ", name)
    if without_parens not in candidates:
        candidates.append(without_parens)
    stripped_suffix = re.sub(
        r"\b(?:Bahnsteig|Bahnsteige|Gleis)\b[^,;/]*",
        " ",
        without_parens,
        flags=re.IGNORECASE,
    )
    if stripped_suffix not in candidates:
        candidates.append(stripped_suffix)

    tokens: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        token = _strip_accents(candidate)
        token = token.replace("ß", "ss")
        token = token.casefold()
        token = re.sub(r"\b(?:bahnhof|bahnhst|bhf|hbf|bf)\b", "", token)
        token = re.sub(r"\b(?:bahnsteig|bahnsteige|gleis)\b", "", token)
        token = token.replace("-", " ").replace("/", " ")
        token = re.sub(r"[^a-z0-9]+", " ", token)
        token = re.sub(r"\s{2,}", " ", token).strip()
        if token and token not in seen:
            seen.add(token)
            tokens.append(token)
        numeric_stripped = re.sub(r"\d+", "", token).strip()
        if numeric_stripped and numeric_stripped not in seen:
            seen.add(numeric_stripped)
            tokens.append(numeric_stripped)
    return tokens


def _harmonize_station_name(name: str) -> str:
    """Return *name* with unified whitespace for consistent lookups."""

    text = "".join(" " if ch in {"\u00a0", "\u2007", "\u202f"} else ch for ch in str(name))
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def _coerce_float_value(value: object | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _store_location(
    locations: dict[str, LocationInfo],
    key: str,
    lat: float,
    lon: float,
    source: str,
) -> None:
    info = locations.get(key)
    if info is None:
        locations[key] = LocationInfo(latitude=lat, longitude=lon, sources={source})
        return
    info.add_source(source)
    # Prefer coordinates from station level data when replacing placeholder zeros
    if (info.latitude, info.longitude) == (0.0, 0.0):
        info.latitude = lat
        info.longitude = lon


def _load_gtfs_locations(path: Path) -> dict[str, LocationInfo]:
    locations: dict[str, LocationInfo] = {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
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
    except FileNotFoundError:
        logger.warning("GTFS stops file not found: %s", path)
    except csv.Error as exc:
        logger.warning("Could not parse GTFS stops file %s: %s", path, exc)
    else:
        if locations:
            logger.info("Loaded %d GTFS stop coordinates", len(locations))
    return locations


def _load_wienerlinien_locations(path: Path) -> dict[str, LocationInfo]:
    locations: dict[str, LocationInfo] = {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            reader = csv.DictReader(handle, delimiter=";")
            for row in reader:
                name = row.get("NAME")
                if name:
                    name = _harmonize_station_name(name)
                lat = _coerce_float_value(row.get("WGS84_LAT"))
                lon = _coerce_float_value(row.get("WGS84_LON"))
                if not name or lat is None or lon is None:
                    continue
                for key in _normalize_location_keys(name):
                    if not key:
                        continue
                    _store_location(locations, key, lat, lon, source="wl")
    except FileNotFoundError:
        logger.warning("Wiener Linien haltepunkte file not found: %s", path)
    except csv.Error as exc:
        logger.warning("Could not parse Wiener Linien haltepunkte file %s: %s", path, exc)
    else:
        if locations:
            logger.info("Loaded %d Wiener Linien coordinates", len(locations))
    return locations


def _build_location_index(
    gtfs_path: Path | None,
    wl_path: Path | None,
) -> dict[str, LocationInfo]:
    locations: dict[str, LocationInfo] = {}
    if gtfs_path:
        locations.update(_load_gtfs_locations(gtfs_path))
    if wl_path:
        wl_locations = _load_wienerlinien_locations(wl_path)
        for key, value in wl_locations.items():
            _store_location(locations, key, value.latitude, value.longitude, source="wl")
    return locations


def _load_existing_station_entries(path: Path) -> dict[int, Mapping[str, object]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        logger.warning("Could not parse existing station directory %s: %s", path, exc)
        return {}

    mapping: dict[int, Mapping[str, object]] = {}
    if isinstance(payload, list):
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            bst_id = entry.get("bst_id")
            if isinstance(bst_id, int):
                mapping[bst_id] = entry
    return mapping


def _restore_existing_metadata(
    stations: Iterable[Station], existing_entries: Mapping[int, Mapping[str, object]]
) -> None:
    for station in stations:
        existing = existing_entries.get(station.bst_id)
        if not existing:
            continue
        vor_id_raw = existing.get("vor_id")
        if isinstance(vor_id_raw, str):
            vor_id = vor_id_raw.strip()
            if vor_id:
                station.vor_id = vor_id
        for key, value in existing.items():
            if key in {"bst_id", "bst_code", "name", "in_vienna", "pendler", "vor_id"}:
                continue
            station.extras[key] = deepcopy(value)


def _looks_like_vienna(text: str | None) -> bool:
    if not text:
        return False
    normalized = text.strip().casefold()
    if not normalized.startswith("wien"):
        return False
    if len(normalized) == 4:
        return True
    return not normalized[4].isalpha()


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


def _parse_included_types(raw: str | None) -> list[str]:
    if raw is None:
        return list(DEFAULT_INCLUDED_TYPES)
    items = [part.strip() for part in raw.split(",") if part.strip()]
    return items or list(DEFAULT_INCLUDED_TYPES)


def _parse_radius(raw: str | None) -> int:
    if raw is None:
        return 2500
    try:
        radius = int(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid PLACES_RADIUS_M=%r – using default 2500", raw)
        return 2500
    return max(1, min(50000, radius))


def _parse_max_results(raw: str | None) -> int:
    if raw is None:
        return 20
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid PLACES_MAX_RESULTS=%r – using default 20", raw)
        return 20
    if value <= 0:
        return 0
    return max(1, min(20, value))


def _parse_float(raw: str | None, *, key: str, default: float) -> float:
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid %s=%r – using default %s", key, raw, default)
        return default


def _parse_int(raw: str | None, *, key: str, default: int) -> int:
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid %s=%r – using default %s", key, raw, default)
        return default


def _parse_bounding_box(raw: str | None) -> BoundingBox | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("BOUNDINGBOX_VIENNA must be valid JSON") from exc
    try:
        return BoundingBox(
            min_lat=float(data["min_lat"]),
            min_lng=float(data["min_lng"]),
            max_lat=float(data["max_lat"]),
            max_lng=float(data["max_lng"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("BOUNDINGBOX_VIENNA must define min_lat/min_lng/max_lat/max_lng") from exc


def _load_tiles_configuration(
    tiles_file: Path | None, env: MutableMapping[str, str]
) -> Sequence[Tile]:
    if tiles_file:
        return load_tiles_from_file(tiles_file)
    return load_tiles_from_env(env.get("PLACES_TILES"))


def _fetch_google_places(client: GooglePlacesClient, tiles: Sequence[Tile]) -> list[Place]:
    places_by_id: dict[str, Place] = {}
    for tile in iter_tiles(tiles):
        logger.info("Fetching Google Places tile at %.5f/%.5f", tile.latitude, tile.longitude)
        try:
            for place in client.iter_nearby([tile]):
                places_by_id.setdefault(place.place_id, place)
        except GooglePlacesTileError as exc:
            logger.warning(
                "Skipping tile %.5f/%.5f due to Google Places error: %s",
                tile.latitude,
                tile.longitude,
                exc,
            )
            continue
    return list(places_by_id.values())


def _merge_google_metadata(
    stations: list[Station],
    places: Sequence[Place],
    merge_config: MergeConfig,
) -> None:
    if not places:
        logger.info("Google Places enrichment returned no places")
        return

    existing_entries = [station.as_dict() for station in stations]
    outcome = merge_places(existing_entries, places, merge_config)

    by_id: dict[int, Mapping[str, object]] = {}
    for entry in outcome.stations:
        bst_id = entry.get("bst_id")
        if isinstance(bst_id, int):
            by_id[bst_id] = entry

    for station in stations:
        merged = by_id.get(station.bst_id)
        if merged:
            station.update_from_entry(merged)

    logger.info(
        "Google Places enrichment updated %d stations; %d suggestions already covered",
        len(outcome.updated_entries),
        len(outcome.skipped_places),
    )

    unmatched = [entry for entry in outcome.new_entries if "bst_id" not in entry]
    if unmatched:
        logger.info(
            "Google Places suggested %d additional stations without bst_id; ignoring",
            len(unmatched),
        )


def _enrich_with_google_places(
    stations: list[Station], *, tiles_file: Path | None
) -> None:
    load_default_env_files()
    env = os.environ

    try:
        api_key = get_places_api_key()
    except SystemExit as exc:  # pragma: no cover - depends on env configuration
        message = exc.args[0] if exc.args else "Missing GOOGLE_ACCESS_ID"
        logger.warning("Skipping Google Places enrichment: %s", message)
        return

    try:
        tiles = _load_tiles_configuration(tiles_file, env)
    except (OSError, ValueError) as exc:
        logger.error("Cannot load Places tile configuration: %s", exc)
        return

    included_types = _parse_included_types(env.get("PLACES_INCLUDED_TYPES"))
    radius_m = _parse_radius(env.get("PLACES_RADIUS_M"))
    max_result_count = _parse_max_results(env.get("PLACES_MAX_RESULTS"))
    language = env.get("PLACES_LANGUAGE", "de")
    region = env.get("PLACES_REGION", "AT")
    timeout_s = _parse_float(env.get("REQUEST_TIMEOUT_S"), key="REQUEST_TIMEOUT_S", default=25.0)
    max_retries = _parse_int(env.get("REQUEST_MAX_RETRIES"), key="REQUEST_MAX_RETRIES", default=4)
    merge_distance = _parse_float(env.get("MERGE_MAX_DIST_M"), key="MERGE_MAX_DIST_M", default=150.0)

    try:
        bounding_box = _parse_bounding_box(env.get("BOUNDINGBOX_VIENNA"))
    except ValueError as exc:
        logger.error("Invalid BOUNDINGBOX_VIENNA configuration: %s", exc)
        return

    client_config = GooglePlacesConfig(
        api_key=api_key,
        included_types=included_types,
        language=language,
        region=region,
        radius_m=radius_m,
        timeout_s=timeout_s,
        max_retries=max_retries,
        max_result_count=max_result_count,
    )
    client = GooglePlacesClient(client_config)

    try:
        places = _fetch_google_places(client, tiles)
    except GooglePlacesPermissionError as exc:
        hint = permission_hint(str(exc))
        if hint:
            logger.error("Google Places denied access: %s | %s", exc, hint)
        else:
            logger.error("Google Places denied access: %s", exc)
        return
    except GooglePlacesError as exc:
        logger.error("Google Places enrichment failed: %s", exc)
        return

    _merge_google_metadata(stations, places, MergeConfig(max_distance_m=merge_distance, bounding_box=bounding_box))


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
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        delimiter = _detect_csv_delimiter(sample)
        reader = csv.DictReader(handle, delimiter=delimiter)
        for row in reader:
            if not isinstance(row, dict):
                continue
            yield _NormalizedCSVRow({key or "": value for key, value in row.items()})


def load_vor_stops(path: Path) -> list[VORStop]:
    try:
        rows = list(_iter_vor_rows(path))
    except FileNotFoundError:
        logger.info("VOR stops file not found: %s", path)
        return []
    except csv.Error as exc:
        logger.warning("Could not parse VOR stops file %s: %s", path, exc)
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
        logger.info("No VOR stops extracted from %s", path)
    else:
        logger.info("Loaded %d VOR stops from %s", len(stops), path)
    return list(stops.values())


def _vor_alias_candidates(stop: VORStop) -> set[str]:
    aliases: set[str] = {stop.name, stop.vor_id}
    if stop.short_name:
        aliases.add(stop.short_name)
    if stop.municipality:
        combined = f"{stop.municipality} {stop.name}".strip()
        aliases.add(combined)
    return {alias for alias in aliases if alias}


def _build_vor_index(stops: Iterable[VORStop]) -> dict[str, list[VORStop]]:
    index: dict[str, list[VORStop]] = {}
    for stop in stops:
        tokens: set[str] = set()
        for alias in _vor_alias_candidates(stop):
            tokens.update(_normalize_location_keys(alias))
        for token in tokens:
            if not token:
                continue
            index.setdefault(token, []).append(stop)
    return index


def _select_vor_stop(station: Station, candidates: list[VORStop]) -> VORStop | None:
    unique: dict[str, VORStop] = {}
    for stop in candidates:
        if stop.vor_id not in unique:
            unique[stop.vor_id] = stop
    stops = list(unique.values())
    if not stops:
        return None
    if len(stops) == 1:
        return stops[0]

    def _is_vienna_stop(stop: VORStop) -> bool:
        return _looks_like_vienna(stop.municipality) or _looks_like_vienna(stop.name)

    if station.in_vienna:
        vienna_stops = [stop for stop in stops if _is_vienna_stop(stop)]
        if len(vienna_stops) == 1:
            return vienna_stops[0]
        if vienna_stops:
            stops = vienna_stops
    else:
        outside = [stop for stop in stops if not _is_vienna_stop(stop)]
        if len(outside) == 1:
            return outside[0]
        if outside:
            stops = outside

    normalized_station = _harmonize_station_name(station.name).casefold()
    name_matches = [
        stop for stop in stops if _harmonize_station_name(stop.name).casefold() == normalized_station
    ]
    if len(name_matches) == 1:
        return name_matches[0]
    return None


def _assign_vor_ids(stations: list[Station], vor_stops: list[VORStop]) -> None:
    if not vor_stops:
        return
    index = _build_vor_index(vor_stops)
    for station in stations:
        if station.vor_id:
            continue
        tokens = _normalize_location_keys(station.name)
        if not tokens:
            continue
        candidates: list[VORStop] = []
        for token in tokens:
            candidates.extend(index.get(token, []))
        if not candidates:
            continue
        selected = _select_vor_stop(station, candidates)
        if selected:
            station.vor_id = selected.vor_id
        else:
            logger.debug(
                "Ambiguous VOR stop candidates for %s (%s)", station.name, station.bst_id
            )


def _harmonize_station_names(
    stations: list[Station],
    existing_entries: Mapping[int, Mapping[str, object]],
) -> None:
    if not existing_entries:
        for station in stations:
            station.name = _harmonize_station_name(station.name)
        return

    for station in stations:
        existing = existing_entries.get(station.bst_id)
        if existing:
            name_raw = existing.get("name")
            if isinstance(name_raw, str) and name_raw.strip():
                canonical = _harmonize_station_name(name_raw)
                if canonical and canonical != station.name:
                    logger.debug(
                        "Using existing name for %s: %s -> %s",
                        station.bst_id,
                        station.name,
                        canonical,
                    )
                station.name = canonical or station.name
                continue
        station.name = _harmonize_station_name(station.name)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download the ÖBB station directory and export a JSON mapping",
    )
    parser.add_argument(
        "--source-url",
        default=DEFAULT_SOURCE_URL,
        help="URL of the Excel workbook to download",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="Path to the JSON file that will be written",
    )
    parser.add_argument(
        "--pendler",
        type=Path,
        metavar="PATH",
        default=DEFAULT_PENDLER_PATH,
        help="Path to the JSON file containing pendler station IDs",
    )
    parser.add_argument(
        "--vor-stops",
        type=Path,
        metavar="PATH",
        default=DEFAULT_VOR_STOPS_PATH,
        help="Path to the VOR stop CSV used for VOR_STATION_IDS",
    )
    parser.add_argument(
        "--gtfs-stops",
        type=Path,
        metavar="PATH",
        default=DEFAULT_GTFS_STOPS_PATH,
        help="Path to a GTFS stops.txt file for coordinate lookup",
    )
    parser.add_argument(
        "--wl-haltepunkte",
        type=Path,
        metavar="PATH",
        default=DEFAULT_WL_HALTEPUNKTE_PATH,
        help="Path to the Wiener Linien haltepunkte CSV for coordinate lookup",
    )
    parser.add_argument(
        "--google-enrich",
        dest="google_enrich",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use Google Places API to enrich station metadata (default: enabled)",
    )
    parser.add_argument(
        "--places-tiles-file",
        type=Path,
        metavar="PATH",
        help="Optional JSON file overriding PLACES_TILES for Google enrichment",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print progress information during the update run",
    )
    return parser.parse_args()


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(message)s")
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def download_workbook(url: str) -> BytesIO:
    logger.info("Downloading workbook: %s", url)
    with session_with_retries(USER_AGENT) as session:
        content = fetch_content_safe(session, url, timeout=REQUEST_TIMEOUT)
        return BytesIO(content)


def _normalize_header(value: object | None) -> str:
    if value is None:
        return ""
    normalized = str(value).strip().lower()
    for token in (" ", "-", "_"):
        normalized = normalized.replace(token, "")
    return normalized


def _match_required_headers(row: Iterable[object]) -> dict[str, int]:
    normalized = [_normalize_header(cell) for cell in row]
    column_map: dict[str, int] = {}
    for field, candidates in HEADER_VARIANTS.items():
        for index, value in enumerate(normalized):
            if any(value == candidate or value.startswith(candidate) for candidate in candidates):
                column_map[field] = index
                break
    return column_map


def _find_header_row(rows: Iterable[tuple[object, ...]]) -> tuple[int, dict[str, int]]:
    for index, row in enumerate(rows, start=1):
        if not row:
            continue
        column_map = _match_required_headers(row)
        if len(column_map) == len(HEADER_VARIANTS):
            return index, column_map
    raise ValueError("Could not identify the header row in the workbook")


def _coerce_bst_id(value: object | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, str):
        digits = value.strip()
        if not digits.isdigit():
            return None
        return int(digits)
    if isinstance(value, (int, float)):
        return int(value)
    return None


def extract_stations(workbook_stream: BytesIO) -> list[Station]:
    workbook = openpyxl.load_workbook(workbook_stream, data_only=True, read_only=True)
    try:
        worksheet = workbook.active
        header_row_index, column_map = _find_header_row(
            worksheet.iter_rows(min_row=1, max_row=25, values_only=True)
        )
        logger.debug("Detected header row at index %s", header_row_index)
        stations: list[Station] = []
        seen_ids: set[int] = set()
        for row in worksheet.iter_rows(min_row=header_row_index + 1, values_only=True):
            if not row:
                continue
            name_cell = row[column_map["name"]] if column_map["name"] < len(row) else None
            code_cell = row[column_map["bst_code"]] if column_map["bst_code"] < len(row) else None
            id_cell = row[column_map["bst_id"]] if column_map["bst_id"] < len(row) else None
            if name_cell is None or code_cell is None:
                continue
            parsed_id = _coerce_bst_id(id_cell)
            if parsed_id is None or parsed_id in seen_ids:
                continue
            station = Station(
                bst_id=parsed_id,
                bst_code=str(code_cell).strip(),
                name=str(name_cell).strip(),
            )
            seen_ids.add(parsed_id)
            stations.append(station)
        stations.sort(key=lambda item: item.bst_id)
        logger.info("Extracted %d stations", len(stations))
        return stations
    finally:
        workbook.close()


def _is_vienna_station(name: str) -> bool:
    text = name.strip()
    if not text.startswith("Wien"):
        return False
    if len(text) == 4:
        return True
    return len(text) > 4 and text[4] in {" ", "-", "/", "("}


def _annotate_station_flags(
    stations: list[Station],
    pendler_ids: set[int],
    locations: Mapping[str, LocationInfo],
) -> None:
    for station in stations:
        info: LocationInfo | None = None
        for key in _normalize_location_keys(station.name):
            info = locations.get(key)
            if info:
                break
        if info:
            station.in_vienna = _is_point_in_vienna(info.latitude, info.longitude)
        else:
            station.in_vienna = _is_vienna_station(station.name)
        pendler = station.bst_id in pendler_ids
        if info and not station.in_vienna and "wl" in info.sources:
            pendler = True
        station.pendler = pendler


def _filter_relevant_stations(stations: list[Station]) -> list[Station]:
    filtered = [station for station in stations if station.in_vienna or station.pendler]
    removed = len(stations) - len(filtered)
    if removed:
        logger.info("Dropping %d stations outside Vienna and commuter belt", removed)
    return filtered


def load_pendler_station_ids(path: Path) -> set[int]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        logger.warning("Pendler station list not found: %s", path)
        return set()
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in pendler station list: {path}") from exc

    if not isinstance(data, list):
        raise ValueError(f"Pendler station list must be a JSON array: {path}")

    pendler_ids: set[int] = set()
    for entry in data:
        if isinstance(entry, bool):
            raise ValueError(
                f"Invalid pendler station identifier (boolean) in {path}: {entry!r}"
            )
        if isinstance(entry, int):
            pendler_ids.add(entry)
            continue
        if isinstance(entry, str):
            token = entry.strip()
            if token.isdigit():
                pendler_ids.add(int(token))
                continue
        raise ValueError(f"Invalid pendler station identifier in {path}: {entry!r}")

    logger.info("Loaded %d pendler station IDs", len(pendler_ids))
    return pendler_ids


def write_json(stations: list[Station], output_path: Path) -> None:
    payload = [station.as_dict() for station in stations]
    # Use atomic_write to prevent partial writes and reduce race conditions.
    with atomic_write(output_path, mode="w", encoding="utf-8", permissions=0o644) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    logger.info("Wrote %s", output_path)


def main() -> None:
    args = parse_args()
    configure_logging(args.verbose)
    _refresh_provider_caches()
    existing_entries = _load_existing_station_entries(args.output)
    workbook_stream = download_workbook(args.source_url)
    stations = extract_stations(workbook_stream)
    _harmonize_station_names(stations, existing_entries)
    _restore_existing_metadata(stations, existing_entries)
    pendler_ids = load_pendler_station_ids(path=args.pendler)
    location_index = _build_location_index(args.gtfs_stops, args.wl_haltepunkte)
    if not location_index:
        logger.warning("No coordinate data available; falling back to name heuristic")
    _annotate_station_flags(stations, pendler_ids, location_index)
    vor_stops = load_vor_stops(args.vor_stops) if args.vor_stops else []
    if vor_stops:
        _assign_vor_ids(stations, vor_stops)
    stations = _filter_relevant_stations(stations)
    if getattr(args, "google_enrich", False):
        _enrich_with_google_places(stations, tiles_file=args.places_tiles_file)
    else:
        logger.info("Skipping Google Places enrichment (--no-google-enrich)")
    write_json(stations, args.output)


if __name__ == "__main__":
    main()
