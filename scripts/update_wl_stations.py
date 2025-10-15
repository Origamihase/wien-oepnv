#!/usr/bin/env python3
"""Merge Wiener Linien CSV exports into the station directory.

The script reads the OGD CSV files ``wienerlinien-ogd-haltepunkte`` and
``wienerlinien-ogd-haltestellen`` (expected to live in ``data/`` by default)
combines the StopIDs with the station level metadata and appends the
resulting entries to ``data/stations.json``.

The JSON entries are tagged with ``"source": "wl"`` so they can easily be
replaced on subsequent runs.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Callable, Iterable, Iterator, List, Sequence


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_is_in_vienna() -> Callable[..., bool]:
    base_dir = _project_root()
    if str(base_dir) not in sys.path:
        sys.path.insert(0, str(base_dir))
    module = import_module("src.utils.stations")
    return module.is_in_vienna


BASE_DIR = _project_root()
is_in_vienna = _load_is_in_vienna()
DEFAULT_HALTEPUNKTE = BASE_DIR / "data" / "wienerlinien-ogd-haltepunkte.csv"
DEFAULT_HALTESTELLEN = BASE_DIR / "data" / "wienerlinien-ogd-haltestellen.csv"
DEFAULT_STATIONS = BASE_DIR / "data" / "stations.json"

log = logging.getLogger("update_wl_stations")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge Wiener Linien stop metadata into stations.json",
    )
    parser.add_argument(
        "--haltepunkte",
        type=Path,
        default=DEFAULT_HALTEPUNKTE,
        help="Path to the haltepunkte CSV export",
    )
    parser.add_argument(
        "--haltestellen",
        type=Path,
        default=DEFAULT_HALTESTELLEN,
        help="Path to the haltestellen CSV export",
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


def _coerce_float(value: str) -> float | None:
    if not value:
        return None
    text = value.strip().replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


@dataclass
class Haltestelle:
    station_id: str
    name: str
    diva: str | None


@dataclass
class Haltepunkt:
    station_id: str
    stop_id: str
    name: str
    latitude: float | None
    longitude: float | None


def _dict_reader(path: Path) -> Iterator[NormalizedRow]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        for row in reader:
            yield NormalizedRow({key or "": value for key, value in row.items()})


def load_haltestellen(path: Path) -> dict[str, Haltestelle]:
    mapping: dict[str, Haltestelle] = {}
    for row in _dict_reader(path):
        station_id = row.get("HALTESTELLEN_ID", "ID")
        name = row.get("NAME")
        diva = row.get("DIVA", "DIVANR") or None
        if not station_id or not name:
            continue
        mapping[station_id] = Haltestelle(
            station_id=station_id,
            name=name,
            diva=diva,
        )
    return mapping


def load_haltepunkte(path: Path) -> List[Haltepunkt]:
    haltepunkt_records: List[Haltepunkt] = []
    for row in _dict_reader(path):
        station_id = row.get("HALTESTELLEN_ID", "ID")
        stop_id = row.get("STOP_ID", "STOPID", "RBL_NUMMER", "RBLNR")
        name = row.get("NAME", "HALTEPUNKTNAME")
        lat = _coerce_float(row.get("WGS84_LAT", "LAT", "GEO_LAT"))
        lon = _coerce_float(row.get("WGS84_LON", "LON", "GEO_LON", "LONG"))
        if not station_id or not stop_id:
            continue
        haltepunkt_records.append(
            Haltepunkt(
                station_id=station_id,
                stop_id=stop_id,
                name=name,
                latitude=lat,
                longitude=lon,
            )
        )
    return haltepunkt_records


def _canonical_name(raw: str) -> str:
    cleaned = re.sub(r"\s+\([^)]*\)", "", raw).strip()
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    if not cleaned:
        cleaned = raw.strip()
    if cleaned.casefold().startswith("wien"):
        base = cleaned
    else:
        base = f"Wien {cleaned}".strip()
    if "(WL)" not in base:
        base = f"{base} (WL)"
    return base


def build_wl_entries(
    haltestellen: dict[str, Haltestelle],
    haltepunkte: Iterable[Haltepunkt],
) -> list[dict[str, object]]:
    grouped: dict[str, list[Haltepunkt]] = {}
    for halt in haltepunkte:
        station = haltestellen.get(halt.station_id)
        if station is None:
            continue
        key = station.diva or station.station_id
        grouped.setdefault(key, []).append(halt)

    entries: list[dict[str, object]] = []
    for diva, stops in grouped.items():
        if not stops:
            continue
        station = haltestellen.get(stops[0].station_id)
        if station is None:
            continue
        station_identifier = station.diva or station.station_id
        aliases = {station.name}
        stops_payload = []
        for stop in stops:
            aliases.add(stop.name)
            aliases.add(stop.stop_id)
            stops_payload.append(
                {
                    "stop_id": stop.stop_id,
                    "name": stop.name,
                    "latitude": stop.latitude,
                    "longitude": stop.longitude,
                }
            )
        if station.diva:
            aliases.add(station.diva)
        aliases.add(f"Wien {station.name}")
        canonical = _canonical_name(station.name)
        coords_checked = False
        in_vienna = False
        for stop in stops:
            if stop.latitude is None or stop.longitude is None:
                continue
            coords_checked = True
            if is_in_vienna(stop.latitude, stop.longitude):
                in_vienna = True
                break
        if not coords_checked:
            log.warning(
                "WL station %s (%s) lacks coordinates; falling back to name lookup",
                station.name,
                station_identifier,
            )
            in_vienna = is_in_vienna(station.name)
        entry = {
            "name": canonical,
            "in_vienna": in_vienna,
            "pendler": False,
            "wl_diva": station_identifier,
            "wl_stops": sorted(
                stops_payload,
                key=lambda item: item["stop_id"],
            ),
            "aliases": sorted(
                {alias for alias in aliases if isinstance(alias, str) and alias.strip()} 
            ),
            "source": "wl",
        }
        entries.append(entry)
    entries.sort(key=lambda item: (str(item.get("name")), str(item.get("wl_diva"))))
    return entries


def merge_into_stations(
    stations_path: Path,
    wl_entries: list[dict[str, object]],
) -> None:
    try:
        with stations_path.open("r", encoding="utf-8") as handle:
            existing = json.load(handle)
    except FileNotFoundError:
        existing = []
    if not isinstance(existing, list):
        raise ValueError("stations.json must contain a JSON array")

    filtered = [entry for entry in existing if entry.get("source") != "wl"]
    log.info("Keeping %d existing non-WL stations", len(filtered))
    filtered.extend(wl_entries)

    with stations_path.open("w", encoding="utf-8") as handle:
        json.dump(filtered, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    log.info("Wrote %d total stations", len(filtered))


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(args.verbose)

    log.info("Reading haltestellen: %s", args.haltestellen)
    haltestellen = load_haltestellen(args.haltestellen)
    log.info("Found %d haltestellen", len(haltestellen))

    log.info("Reading haltepunkte: %s", args.haltepunkte)
    haltepunkte = load_haltepunkte(args.haltepunkte)
    log.info("Found %d haltepunkte", len(haltepunkte))

    wl_entries = build_wl_entries(haltestellen, haltepunkte)
    log.info("Prepared %d WL station entries", len(wl_entries))

    merge_into_stations(args.stations, wl_entries)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
