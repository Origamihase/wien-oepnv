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
from typing import Iterable, Mapping

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

    def as_dict(self) -> dict[str, object]:
        return {
            "bst_id": self.bst_id,
            "bst_code": self.bst_code,
            "name": self.name,
            "in_vienna": self.in_vienna,
            "pendler": self.pendler,
        }


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
    pendler_ids = load_pendler_station_ids(path=args.pendler)
    location_index = _build_location_index(args.gtfs_stops, args.wl_haltepunkte)
    if not location_index:
        logger.warning("No coordinate data available; falling back to name heuristic")
    _annotate_station_flags(stations, pendler_ids, location_index)
    stations = _filter_relevant_stations(stations)
    write_json(stations, args.output)


if __name__ == "__main__":
    main()
