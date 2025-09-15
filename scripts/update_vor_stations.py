#!/usr/bin/env python3
"""Merge VOR stop metadata into the station directory."""
from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:  # pragma: no cover - convenience for module execution
    from src.utils.stations import is_in_vienna
except ModuleNotFoundError:  # pragma: no cover - fallback when installed as package
    from utils.stations import is_in_vienna  # type: ignore
DEFAULT_SOURCE = BASE_DIR / "data" / "vor-haltestellen.csv"
DEFAULT_STATIONS = BASE_DIR / "data" / "stations.json"

log = logging.getLogger("update_vor_stations")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge VOR stop metadata into stations.json",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help="Path to the VOR CSV/GTFS export",
    )
    parser.add_argument(
        "--stations",
        type=Path,
        default=DEFAULT_STATIONS,
        help="stations.json that should be updated",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging output",
    )
    return parser.parse_args(argv)


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(message)s")


def _normalize_key(value: str | None) -> str:
    if value is None:
        return ""
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


class NormalizedRow:
    """Wrapper around a CSV row that allows fuzzy column access."""

    def __init__(self, row: dict[str, str | None]):
        self._row = row
        self._map = {_normalize_key(key): key for key in row if key}

    def get(self, *candidates: str) -> str:
        for candidate in candidates:
            key = self._map.get(_normalize_key(candidate))
            if key is None:
                continue
            value = self._row.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return ""


def _detect_delimiter(sample: str) -> str:
    semicolons = sample.count(";")
    commas = sample.count(",")
    if semicolons >= commas and semicolons > 0:
        return ";"
    if commas > 0:
        return ","
    return ";"


def _dict_reader(path: Path) -> Iterator[NormalizedRow]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        delimiter = _detect_delimiter(sample)
        reader = csv.DictReader(handle, delimiter=delimiter)
        for row in reader:
            yield NormalizedRow({key or "": value for key, value in row.items()})


def _coerce_float(value: str) -> float | None:
    if not value:
        return None
    text = value.strip().replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


@dataclass
class VORStop:
    vor_id: str
    name: str
    latitude: float | None
    longitude: float | None
    municipality: str | None = None
    short_name: str | None = None
    global_id: str | None = None
    gtfs_stop_id: str | None = None


_ID_CANDIDATES = (
    "StopPointId",
    "StopID",
    "Stop_Id",
    "StopPoint",  # fallback for some exports
    "ID",
)


def load_vor_stops(path: Path) -> list[VORStop]:
    stops: dict[str, VORStop] = {}
    for row in _dict_reader(path):
        vor_id = row.get(*_ID_CANDIDATES)
        if not vor_id:
            vor_id = row.get("StopPointGlobalId", "GlobalId", "GlobalID")
        if not vor_id:
            continue
        name = row.get("StopPointName", "Name", "StopName", "Bezeichnung")
        if not name:
            continue
        municipality = row.get("Municipality", "Gemeinde", "City", "Ort") or None
        latitude = _coerce_float(
            row.get(
                "Latitude",
                "Lat",
                "WGS84_LAT",
                "Geo_Lat",
                "Y",
                "Koord_Y",
            )
        )
        longitude = _coerce_float(
            row.get(
                "Longitude",
                "Lon",
                "WGS84_LON",
                "Geo_Lon",
                "X",
                "Koord_X",
            )
        )
        short_name = row.get("StopPointShortName", "ShortName", "Kurzname") or None
        global_id = row.get("StopPointGlobalId", "GlobalId", "GlobalID") or None
        gtfs_stop_id = row.get("Stop_Id", "StopID", "GTFS_Stop_ID", "GTFSStopID") or None
        stops[vor_id] = VORStop(
            vor_id=vor_id,
            name=name,
            latitude=latitude,
            longitude=longitude,
            municipality=municipality,
            short_name=short_name,
            global_id=global_id,
            gtfs_stop_id=gtfs_stop_id,
        )
    return list(stops.values())


def _looks_like_vienna(text: str | None) -> bool:
    if not text:
        return False
    normalized = text.strip().casefold()
    if not normalized.startswith("wien"):
        return False
    if len(normalized) == 4:
        return True
    return not normalized[4].isalpha()


def _canonical_vor_name(name: str) -> str:
    cleaned = re.sub(r"\s{2,}", " ", name.strip())
    if not cleaned:
        cleaned = name.strip()
    if "(VOR)" not in cleaned:
        cleaned = f"{cleaned} (VOR)"
    return cleaned


def _build_aliases(stop: VORStop) -> list[str]:
    aliases: set[str] = set()
    for candidate in (
        stop.name,
        stop.vor_id,
        stop.short_name,
        stop.global_id,
        stop.gtfs_stop_id if stop.gtfs_stop_id != stop.vor_id else None,
    ):
        if not candidate:
            continue
        text = str(candidate).strip()
        if text:
            aliases.add(text)
    municipality = (stop.municipality or "").strip()
    if municipality:
        reference = stop.name.casefold()
        if municipality.casefold() not in reference:
            combined = f"{municipality} {stop.name}".strip()
            if combined:
                aliases.add(combined)
    return sorted(aliases)


def build_vor_entries(stops: Iterable[VORStop]) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for stop in stops:
        canonical = _canonical_vor_name(stop.name)
        if stop.latitude is not None and stop.longitude is not None:
            in_vienna = is_in_vienna(stop.latitude, stop.longitude)
        else:
            in_vienna = _looks_like_vienna(stop.municipality) or _looks_like_vienna(stop.name)
            log.warning(
                "Missing coordinates for VOR stop %s (%s); falling back to heuristics",
                stop.name,
                stop.vor_id,
            )
        entry = {
            "name": canonical,
            "in_vienna": in_vienna,
            "pendler": False,
            "vor_id": stop.vor_id,
            "latitude": stop.latitude,
            "longitude": stop.longitude,
            "aliases": _build_aliases(stop),
            "source": "vor",
        }
        entries.append(entry)
    entries.sort(key=lambda item: (str(item.get("name")), str(item.get("vor_id"))))
    return entries


def merge_into_stations(stations_path: Path, vor_entries: list[dict[str, object]]) -> None:
    try:
        with stations_path.open("r", encoding="utf-8") as handle:
            existing = json.load(handle)
    except FileNotFoundError:
        existing = []
    if not isinstance(existing, list):
        raise ValueError("stations.json must contain a JSON array")

    non_vor: list[dict[str, object]] = []
    wl_entries: list[dict[str, object]] = []
    for entry in existing:
        if not isinstance(entry, dict):
            continue
        source = entry.get("source")
        if source == "vor":
            continue
        if source == "wl":
            wl_entries.append(entry)
        else:
            non_vor.append(entry)

    merged = non_vor + vor_entries + wl_entries

    with stations_path.open("w", encoding="utf-8") as handle:
        json.dump(merged, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    log.info(
        "Wrote %d total stations (%d VOR entries)",
        len(merged),
        len(vor_entries),
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(args.verbose)

    log.info("Reading VOR stops: %s", args.source)
    vor_stops = load_vor_stops(args.source)
    log.info("Found %d VOR stops", len(vor_stops))

    vor_entries = build_vor_entries(vor_stops)
    log.info("Prepared %d VOR station entries", len(vor_entries))

    merge_into_stations(args.stations, vor_entries)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
