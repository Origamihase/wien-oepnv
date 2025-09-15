#!/usr/bin/env python3
"""Download and parse the ÖBB station directory Excel file.

The script exports a simplified JSON mapping (bst_id, bst_code, name,
``in_vienna``) that is used throughout the project. The data is obtained from the
official ÖBB Open-Data portal.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Iterable

import openpyxl
import requests

DEFAULT_SOURCE_URL = (
    "https://data.oebb.at/dam/jcr:fce22daf-0dd8-4a15-80b4-dbca6e80ce38/"
    "Verzeichnis%20der%20Verkehrsstationen.xlsx"
)
BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_PATH = BASE_DIR / "data" / "stations.json"
DEFAULT_VIENNA_IDS_PATH = BASE_DIR / "data" / "vienna_bst_ids.json"
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


@dataclass(frozen=True)
class Station:
    """Representation of a single station entry."""

    bst_id: int
    bst_code: str
    name: str
    in_vienna: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "bst_id": self.bst_id,
            "bst_code": self.bst_code,
            "name": self.name,
            "in_vienna": self.in_vienna,
        }


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
        "--vienna-ids",
        type=Path,
        default=DEFAULT_VIENNA_IDS_PATH,
        help="Path to the JSON file containing BST-IDs for stations in Vienna",
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


def _parse_bst_ids(value: object | None) -> list[int]:
    if value is None:
        return []
    if isinstance(value, (int, float)):
        return [int(value)]
    if isinstance(value, str):
        tokens = re.split(r"[;,]", value)
        ids: list[int] = []
        for token in tokens:
            digits = token.strip()
            if digits.isdigit():
                ids.append(int(digits))
        return ids
    return []


def _split_bst_codes(value: object | None) -> list[str]:
    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []
    parts = [part.strip() for part in text.split(";")]
    return [part for part in parts if part]


def load_vienna_ids(path: Path) -> set[int]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        logger.warning("Vienna BST-ID list %s not found; treating as empty", path)
        return set()
    if not isinstance(payload, list):
        raise ValueError(f"Invalid Vienna BST-ID list: expected list, got {type(payload)!r}")
    ids: set[int] = set()
    for item in payload:
        if isinstance(item, bool):  # avoid treating booleans as integers
            continue
        try:
            ids.add(int(item))
        except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
            raise ValueError(f"Invalid BST-ID {item!r} in {path}") from exc
    return ids


def extract_stations(workbook_stream: BytesIO, vienna_ids: set[int]) -> list[Station]:
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
            bst_ids = _parse_bst_ids(id_cell)
            if not bst_ids:
                continue
            bst_codes = _split_bst_codes(code_cell) or [str(code_cell).strip()]
            if len(bst_codes) == len(bst_ids):
                code_pairs = zip(bst_ids, bst_codes)
            else:
                default_code = bst_codes[0] if bst_codes else ""
                code_pairs = ((bst_id, default_code) for bst_id in bst_ids)
            for bst_id, bst_code in code_pairs:
                if bst_id in seen_ids:
                    continue
                station = Station(
                    bst_id=bst_id,
                    bst_code=bst_code,
                    name=str(name_cell).strip(),
                    in_vienna=bst_id in vienna_ids,
                )
                seen_ids.add(bst_id)
                stations.append(station)
        stations.sort(key=lambda item: item.bst_id)
        logger.info("Extracted %d stations", len(stations))
        return stations
    finally:
        workbook.close()


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
    vienna_ids = load_vienna_ids(args.vienna_ids)
    stations = extract_stations(workbook_stream, vienna_ids)
    write_json(stations, args.output)


if __name__ == "__main__":
    main()
