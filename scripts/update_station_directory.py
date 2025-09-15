#!/usr/bin/env python3
"""Download and parse the ÖBB station directory Excel file.

The script exports a simplified JSON mapping (bst_id, bst_code, name) that is used
throughout the project. The data is obtained from the official ÖBB Open-Data
portal.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Dict, Iterable, Tuple

import openpyxl
import requests

from scripts.gtfs import DEFAULT_GTFS_STOP_PATH, read_gtfs_stops

try:  # pragma: no cover - imported for script execution
    from src.utils.stations import is_in_vienna as is_point_in_vienna  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - fallback when running as module
    from utils.stations import is_in_vienna as is_point_in_vienna  # type: ignore

DEFAULT_SOURCE_URL = (
    "https://data.oebb.at/dam/jcr:fce22daf-0dd8-4a15-80b4-dbca6e80ce38/"
    "Verzeichnis%20der%20Verkehrsstationen.xlsx"
)
DEFAULT_OUTPUT_PATH = Path(__file__).resolve().parents[1] / "data" / "stations.json"
DEFAULT_PENDLER_PATH = Path(__file__).resolve().parents[1] / "data" / "pendler_bst_ids.json"
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


@dataclass(slots=True)
class CoordinateIndex:
    """Coordinate lookup tables for station metadata."""

    by_id: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    by_code: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    by_name: Dict[str, Tuple[float, float]] = field(default_factory=dict)

    def lookup(self, station: Station) -> Tuple[float, float] | None:
        """Return coordinates for *station* if available."""

        coord = self.by_id.get(str(station.bst_id))
        if coord:
            return coord
        code = _normalize_code(station.bst_code)
        if code:
            coord = self.by_code.get(code)
            if coord:
                return coord
        for token in _name_variants(station.name):
            coord = self.by_name.get(token)
            if coord:
                return coord
        return None


_NAME_PAREN_RE = re.compile(r"\s*\([^)]*\)\s*")
_WHITESPACE_RE = re.compile(r"\s{2,}")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9 ]+")
_CODE_RE = re.compile(r"\s+")


def _strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def _normalize_name_token(value: str) -> str:
    text = _strip_accents(value).replace("ß", "ss").lower()
    text = text.replace("hauptbahnhof", "hbf")
    text = _NAME_PAREN_RE.sub(" ", text)
    text = text.replace("-", " ").replace("/", " ")
    text = _NON_ALNUM_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip()


def _name_variants(value: str) -> list[str]:
    base = _normalize_name_token(value)
    if not base:
        return []
    variants: set[str] = {base}
    tokens = base.split()
    if tokens and tokens[0] == "wien":
        variants.add(" ".join(tokens[1:]).strip())
    if tokens and tokens[-1] == "wien":
        variants.add(" ".join(tokens[:-1]).strip())
    removal_terms = {"bahnhof", "bahnhst", "bahnhst.", "bhf", "hbf"}
    for term in removal_terms.copy():
        removal_terms.add(term.rstrip("."))
    for term in removal_terms:
        for variant in list(variants):
            parts = [part for part in variant.split() if part != term]
            if len(parts) != len(variant.split()):
                candidate = " ".join(parts).strip()
                if candidate:
                    variants.add(candidate)
    cleaned = {_WHITESPACE_RE.sub(" ", variant).strip() for variant in variants}
    return [variant for variant in cleaned if variant]


def _normalize_code(value: str) -> str:
    text = _strip_accents(value).lower()
    return _CODE_RE.sub("", text)


def load_coordinate_index() -> CoordinateIndex:
    """Load coordinates from reference data for use during annotation."""

    index = CoordinateIndex()
    try:
        stops = read_gtfs_stops()
    except FileNotFoundError:
        logger.warning("GTFS stops.txt not found: %s", DEFAULT_GTFS_STOP_PATH)
        return index
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not read GTFS stops: %s", exc)
        return index

    for stop in stops.values():
        lat = stop.stop_lat
        lon = stop.stop_lon
        if lat is None or lon is None:
            continue
        if stop.parent_station and (":" in stop.stop_id or stop.location_type == 0):
            continue
        if stop.stop_id and stop.stop_id not in index.by_id:
            index.by_id[stop.stop_id] = (lat, lon)
        if stop.stop_code:
            code = _normalize_code(stop.stop_code)
            if code and code not in index.by_code:
                index.by_code[code] = (lat, lon)
        for token in _name_variants(stop.stop_name):
            if token and token not in index.by_name:
                index.by_name[token] = (lat, lon)

    logger.debug(
        "Loaded %d GTFS reference coordinates", len(index.by_name) or len(index.by_id)
    )
    return index


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
    stations: list[Station], pendler_ids: set[int], coordinates: CoordinateIndex
) -> None:
    for station in stations:
        coord = coordinates.lookup(station)
        if coord is not None:
            latitude, longitude = coord
            station.in_vienna = is_point_in_vienna(latitude, longitude)
        else:
            station.in_vienna = _is_vienna_station(station.name)
        station.pendler = station.bst_id in pendler_ids


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
    workbook_stream = download_workbook(args.source_url)
    stations = extract_stations(workbook_stream)
    pendler_ids = load_pendler_station_ids(DEFAULT_PENDLER_PATH)
    coordinate_index = load_coordinate_index()
    _annotate_station_flags(stations, pendler_ids, coordinate_index)
    write_json(stations, args.output)


if __name__ == "__main__":
    main()
