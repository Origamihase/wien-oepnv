#!/usr/bin/env python3
"""Download and parse the ÖBB station directory Excel file.

The script exports a simplified JSON mapping that can be consumed by the
application and automated workflows. Only the columns that are required in the
rest of the project (`bst_id`, `bst_code`, `name`) are kept.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Iterable, List

import openpyxl
import requests

DEFAULT_SOURCE_URL = (
    "https://data.oebb.at/dam/jcr:fce22daf-0dd8-4a15-80b4-dbca6e80ce38/"
    "Verzeichnis%20der%20Verkehrsstationen.xlsx"
)
DEFAULT_OUTPUT_PATH = Path(__file__).resolve().parents[1] / "data" / "stations.json"
REQUEST_TIMEOUT = 30  # seconds


@dataclass(frozen=True)
class Station:
    """Representation of a single station entry."""

    bst_id: int
    bst_code: str
    name: str

    def as_dict(self) -> dict[str, object]:
        return {"bst_id": self.bst_id, "bst_code": self.bst_code, "name": self.name}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update the ÖBB station directory JSON")
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
    return parser.parse_args()


def download_workbook(url: str) -> BytesIO:
    response = requests.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return BytesIO(response.content)


def _find_header_row(rows: Iterable[tuple]) -> int:
    for index, row in enumerate(rows, start=1):
        if not row or row[0] is None:
            continue
        first_cell = str(row[0]).strip().lower()
        third_cell = str(row[2]).strip().lower() if len(row) > 2 and row[2] is not None else ""
        if first_cell == "verkehrsstation" and third_cell == "bst id":
            return index
    raise ValueError("Could not identify the header row in the workbook")


def extract_stations(workbook_stream: BytesIO) -> List[Station]:
    workbook = openpyxl.load_workbook(workbook_stream, data_only=True, read_only=True)
    try:
        worksheet = workbook.active
        header_row_index = _find_header_row(
            worksheet.iter_rows(min_row=1, max_row=25, values_only=True)
        )
        stations: list[Station] = []
        seen_ids: set[int] = set()
        for row in worksheet.iter_rows(min_row=header_row_index + 1, values_only=True):
            name, bst_code, bst_id, *_rest = row
            if name is None or bst_code is None or bst_id in (None, ""):
                continue
            if isinstance(bst_id, str):
                bst_id_str = bst_id.strip()
                if not bst_id_str.isdigit():
                    continue
                parsed_id = int(bst_id_str)
            elif isinstance(bst_id, (int, float)):
                parsed_id = int(bst_id)
            else:
                continue
            station = Station(
                bst_id=parsed_id,
                bst_code=str(bst_code).strip(),
                name=str(name).strip(),
            )
            if station.bst_id in seen_ids:
                continue
            seen_ids.add(station.bst_id)
            stations.append(station)
        stations.sort(key=lambda item: item.bst_id)
        return stations
    finally:
        workbook.close()


def write_json(stations: List[Station], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = [station.as_dict() for station in stations]
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def main() -> None:
    args = parse_args()
    workbook_stream = download_workbook(args.source_url)
    stations = extract_stations(workbook_stream)
    write_json(stations, args.output)


if __name__ == "__main__":
    main()
