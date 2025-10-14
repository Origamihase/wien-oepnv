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
import re
import sys
import unicodedata
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Iterable, Mapping, MutableMapping

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
    from src.utils.stations import is_in_vienna as _is_point_in_vienna
except ModuleNotFoundError:  # pragma: no cover - fallback when installed as package
    from utils.stations import is_in_vienna as _is_point_in_vienna  # type: ignore

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


@dataclass
class Station:
    """Representation of a single station entry."""

    bst_id: int
    bst_code: str
    name: str
    in_vienna: bool = False
    pendler: bool = False
    vor_id: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "bst_id": self.bst_id,
            "bst_code": self.bst_code,
            "name": self.name,
            "in_vienna": self.in_vienna,
            "pendler": self.pendler,
            **({"vor_id": self.vor_id} if self.vor_id else {}),
        }


@dataclass
class VORStop:
    """Minimal representation of a VOR stop for ID matching."""

    vor_id: str
    name: str
    municipality: str | None = None
    short_name: str | None = None

    def alias_tokens(self) -> set[str]:
        """Return normalized tokens for all aliases of this stop.

        The tokens are precomputed once per stop so that repeated lookups while
        iterating over stations stay efficient.
        """

        aliases: set[str] = {self.name, self.vor_id}
        if self.short_name:
            aliases.add(self.short_name)
        if self.municipality:
            combined = f"{self.municipality} {self.name}".strip()
            aliases.add(combined)
        tokens: set[str] = set()
        for alias in aliases:
            for token in _normalize_location_keys(alias):
                tokens.add(token)
                if " " in token:
                    tokens.update(part for part in token.split(" ") if part)
        return {token for token in tokens if token}


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


def _normalize_casefold(value: str | None) -> str:
    if not value:
        return ""
    return _harmonize_station_name(value).casefold()


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
            existing = locations.get(key)
            if existing is None:
                locations[key] = value
                continue
            existing.add_source("wl")
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


def _build_vor_index(stops: Iterable[VORStop]) -> dict[str, list[VORStop]]:
    index: dict[str, list[VORStop]] = {}
    for stop in stops:
        tokens: set[str] = set()
        tokens.update(stop.alias_tokens())
        for token in tokens:
            if not token:
                continue
            index.setdefault(token, []).append(stop)
    return index


def _select_vor_stop(
    station: Station,
    candidates: list[VORStop],
    station_tokens: set[str],
    alias_token_cache: MutableMapping[str, set[str]],
) -> VORStop | None:
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

    normalized_station = _normalize_casefold(station.name)
    scored: list[tuple[int, tuple[int, int, int, int, int, int], str, VORStop]] = []
    for stop in stops:
        alias_tokens = alias_token_cache.get(stop.vor_id)
        if alias_tokens is None:
            alias_tokens = stop.alias_tokens()
            alias_token_cache[stop.vor_id] = alias_tokens
        overlap = len(station_tokens & alias_tokens)
        name_match = int(_normalize_casefold(stop.name) == normalized_station)
        short_match = int(_normalize_casefold(stop.short_name) == normalized_station)
        municipality_tokens = set(_normalize_location_keys(stop.municipality)) if stop.municipality else set()
        municipality_match = int(bool(municipality_tokens & station_tokens))
        vienna_alignment = int(_is_vienna_stop(stop) == station.in_vienna)
        score = (name_match * 50) + (short_match * 30) + (municipality_match * 10) + (vienna_alignment * 5) + overlap
        score_meta = (
            name_match,
            short_match,
            municipality_match,
            vienna_alignment,
            overlap,
            -len(alias_tokens),
        )
        scored.append((score, score_meta, stop.vor_id, stop))

    if not scored:
        return None
    scored.sort(reverse=True)
    top_score, top_meta, _, top_stop = scored[0]
    if top_score == 0:
        return None
    # Detect score ties to keep ambiguity safeguards intact
    for score, meta, _, stop in scored[1:]:
        if score != top_score:
            break
        if meta == top_meta:
            logger.debug(
                "Ambiguous VOR stop selection for %s (%s): %s and %s scored equally",
                station.name,
                station.bst_id,
                top_stop.vor_id,
                stop.vor_id,
            )
            return None
    return top_stop


def _assign_vor_ids(stations: list[Station], vor_stops: list[VORStop]) -> None:
    if not vor_stops:
        return
    index = _build_vor_index(vor_stops)
    alias_token_cache: dict[str, set[str]] = {stop.vor_id: stop.alias_tokens() for stop in vor_stops}
    for station in stations:
        tokens = _normalize_location_keys(station.name)
        if not tokens:
            continue
        token_set = set(tokens)
        candidates: list[VORStop] = []
        for token in tokens:
            candidates.extend(index.get(token, []))
        if not candidates:
            continue
        selected = _select_vor_stop(station, candidates, token_set, alias_token_cache)
        if not selected:
            logger.debug(
                "Ambiguous VOR stop candidates for %s (%s)", station.name, station.bst_id
            )
            continue
        if station.vor_id == selected.vor_id:
            continue
        if station.vor_id:
            logger.debug(
                "Updating VOR ID for %s (%s): %s -> %s",
                station.name,
                station.bst_id,
                station.vor_id,
                selected.vor_id,
            )
        station.vor_id = selected.vor_id


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
    response = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    return BytesIO(response.content)


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
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = [station.as_dict() for station in stations]
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    logger.info("Wrote %s", output_path)


def main() -> None:
    args = parse_args()
    configure_logging(args.verbose)
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
    write_json(stations, args.output)


if __name__ == "__main__":
    main()
