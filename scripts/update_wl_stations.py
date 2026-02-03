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
from typing import Callable, Iterable, Iterator, List, Mapping, Sequence


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
DEFAULT_VOR_MAPPING = BASE_DIR / "data" / "vor-haltestellen.mapping.json"

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
        "--vor-mapping",
        type=Path,
        default=DEFAULT_VOR_MAPPING,
        help="Optional vor-haltestellen mapping to enrich WL stations with VOR identifiers",
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


def _derive_bst_id(identifier: str | None) -> int | None:
    if not identifier:
        return None
    digits = re.sub(r"\D", "", identifier)
    if not digits:
        return None
    trimmed = digits[-8:]
    return int(f"9{trimmed.zfill(8)}")


def _derive_bst_code(name: str, identifier: str | None) -> str | None:
    cleaned = re.sub(r"\(WL\)", "", name).strip()
    cleaned = re.sub(r"(?i)^wien\s+", "", cleaned).strip()
    tokens = [token for token in re.split(r"[^A-Za-z0-9ÄÖÜäöüß]+", cleaned) if token]
    if tokens:
        primary = tokens[0][:3]
        if primary:
            return f"WL-{primary.upper()}"
    if identifier:
        digits = re.sub(r"\D", "", identifier)
        if digits:
            return f"WL-{digits[-3:]}"
    return None


def _aggregate_coordinates(stops: Iterable[Haltepunkt]) -> tuple[float | None, float | None]:
    latitudes: list[float] = []
    longitudes: list[float] = []
    for stop in stops:
        if stop.latitude is None or stop.longitude is None:
            continue
        latitudes.append(stop.latitude)
        longitudes.append(stop.longitude)
    if not latitudes or not longitudes:
        return None, None
    avg_lat = round(sum(latitudes) / len(latitudes), 6)
    avg_lon = round(sum(longitudes) / len(longitudes), 6)
    return avg_lat, avg_lon


def _alias_variants(
    station_name: str, canonical: str, resolved: str | None
) -> set[str]:
    base = f"Wien {station_name}".strip()
    variants = {
        canonical,
        base,
        f"{base} (WL)",
        f"{base} U",
        f"{base} U (VOR)",
        f"{base} Bahnhof",
        f"Bahnhof {base}",
        f"{base} Station",
    }
    english_base = base
    if base.lower().startswith("wien "):
        english_base = f"Vienna {base[5:]}".strip()
        variants.update(
            {
                english_base,
                f"{english_base} (WL)",
                f"{english_base} U",
                f"{english_base} U (VOR)",
                f"{english_base} Station",
            }
        )
    variants.add(base.replace(" ", "-"))
    variants.add(canonical.replace(" ", "-"))
    if resolved:
        variants.add(resolved)
        variants.add(f"{resolved} (VOR)")
    return {variant for variant in variants if variant.strip()}


def load_vor_mapping(path: Path) -> dict[str, Mapping[str, object]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        log.info("No VOR mapping found at %s", path)
        return {}
    except json.JSONDecodeError as exc:
        log.warning("Could not parse VOR mapping %s: %s", path, exc)
        return {}
    mapping: dict[str, Mapping[str, object]] = {}
    if not isinstance(raw, list):
        return mapping
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        candidates = set()
        for key in ("station_name", "resolved_name"):
            text = str(entry.get(key) or "").strip()
            if text:
                candidates.add(_normalize_key(text))
        vor_id = str(entry.get("vor_id") or "").strip()
        if vor_id:
            candidates.add(_normalize_key(vor_id))
        for candidate in candidates:
            if candidate:
                mapping[candidate] = entry
    return mapping


def build_wl_entries(
    haltestellen: dict[str, Haltestelle],
    haltepunkte: Iterable[Haltepunkt],
    vor_mapping: Mapping[str, Mapping[str, object]] | None = None,
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
        aliases.add(canonical)
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
        latitude, longitude = _aggregate_coordinates(stops)
        vor_entry: Mapping[str, object] | None = None
        if vor_mapping:
            for candidate in (
                canonical,
                station.name,
                f"Wien {station.name}",
                station_identifier,
            ):
                key = _normalize_key(str(candidate))
                if key and key in vor_mapping:
                    vor_entry = vor_mapping[key]
                    break
        resolved_name = ""
        if vor_entry:
            vor_id = str(vor_entry.get("vor_id") or "").strip()
            if vor_id:
                aliases.add(vor_id)
            resolved_name = str(vor_entry.get("resolved_name") or "").strip()
            if resolved_name:
                aliases.add(resolved_name)
            if latitude is None or longitude is None:
                lat_val = vor_entry.get("latitude")
                lon_val = vor_entry.get("longitude")
                if isinstance(lat_val, (int, float)) and isinstance(lon_val, (int, float)):
                    latitude = round(float(lat_val), 6)
                    longitude = round(float(lon_val), 6)
        aliases.update(_alias_variants(station.name, canonical, resolved_name or None))
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
        bst_id = _derive_bst_id(station_identifier)
        if bst_id is not None:
            entry["bst_id"] = bst_id
        bst_code = _derive_bst_code(canonical, station_identifier)
        if bst_code:
            entry["bst_code"] = bst_code
        if latitude is not None and longitude is not None:
            entry["latitude"] = latitude
            entry["longitude"] = longitude
        if vor_entry:
            vor_id = str(vor_entry.get("vor_id") or "").strip()
            if vor_id:
                entry["vor_id"] = vor_id
        entries.append(entry)
    entries.sort(key=lambda item: (str(item.get("name")), str(item.get("wl_diva"))))
    return entries


def _normalize_sources(value: object | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        candidates = value.split(",")
    elif isinstance(value, Iterable):  # pragma: no cover - defensive guard
        candidates = list(value)
    else:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        text = str(item).strip()
        if not text:
            continue
        if text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _merge_sources(*values: object | None) -> str:
    merged: list[str] = []
    seen: set[str] = set()
    for value in values:
        for item in _normalize_sources(value):
            if item in seen:
                continue
            seen.add(item)
            merged.append(item)
    return ", ".join(merged)


def _ensure_sorted_aliases(entry: dict[str, object]) -> None:
    aliases = entry.get("aliases")
    if not isinstance(aliases, list):
        return
    unique: set[str] = set()
    cleaned: list[str] = []
    for alias in aliases:
        if not isinstance(alias, str):
            continue
        text = alias.strip()
        if not text or text in unique:
            continue
        unique.add(text)
        cleaned.append(text)
    cleaned.sort()
    entry["aliases"] = cleaned


def _merge_wl_payload(target: dict[str, object], payload: Mapping[str, object]) -> None:
    if payload.get("wl_diva"):
        target["wl_diva"] = payload["wl_diva"]

    wl_stops = payload.get("wl_stops")
    if isinstance(wl_stops, list):
        target["wl_stops"] = wl_stops

    target["source"] = _merge_sources(target.get("source"), payload.get("source"), "wl")

    existing_aliases: list[str] = []
    if isinstance(target.get("aliases"), list):
        existing_aliases = list(target.get("aliases") or [])
    incoming_aliases = []
    if isinstance(payload.get("aliases"), list):
        incoming_aliases = list(payload.get("aliases") or [])
    target["aliases"] = existing_aliases + incoming_aliases
    _ensure_sorted_aliases(target)

    if target.get("latitude") in (None, "") and payload.get("latitude") is not None:
        target["latitude"] = payload["latitude"]
    if target.get("longitude") in (None, "") and payload.get("longitude") is not None:
        target["longitude"] = payload["longitude"]


def _lookup_candidates(index: Mapping[str, dict[str, object]], key: object | None) -> dict[str, object] | None:
    if key is None:
        return None
    text = str(key).strip()
    if not text:
        return None
    return index.get(text)


def merge_into_stations(
    stations_path: Path,
    wl_entries: list[dict[str, object]],
) -> None:
    try:
        with stations_path.open("r", encoding="utf-8") as handle:
            raw_data = json.load(handle)
    except FileNotFoundError:
        raw_data = []

    existing: list[dict[str, object]] = []
    is_wrapped = False

    if isinstance(raw_data, list):
        existing = raw_data
    elif isinstance(raw_data, dict) and isinstance(raw_data.get("stations"), list):
        existing = raw_data["stations"]  # type: ignore[assignment]
        is_wrapped = True
    else:
        raise ValueError("stations.json must contain a JSON array or a dict with a 'stations' array")

    filtered: list[dict[str, object]] = []
    vor_index: dict[str, dict[str, object]] = {}
    bst_index: dict[str, dict[str, object]] = {}
    name_index: dict[str, dict[str, object]] = {}

    for entry in existing:
        source = entry.get("source")
        if isinstance(source, str) and source.strip() == "wl":
            continue
        filtered.append(entry)

        vor_id = entry.get("vor_id")
        if vor_id is not None:
            key = str(vor_id).strip()
            if key and key not in vor_index:
                vor_index[key] = entry

        bst_id = entry.get("bst_id")
        if bst_id is not None:
            key = str(bst_id).strip()
            if key and key not in bst_index:
                bst_index[key] = entry

        name = entry.get("name")
        if isinstance(name, str):
            key = _normalize_key(name)
            if key and key not in name_index:
                name_index[key] = entry

    log.info("Keeping %d existing non-WL stations", len(filtered))

    unmatched: list[dict[str, object]] = []
    for payload in wl_entries:
        merged_into: dict[str, object] | None = None

        vor_id = payload.get("vor_id")
        merged_into = _lookup_candidates(vor_index, vor_id)

        if merged_into is None:
            bst_id = payload.get("bst_id")
            merged_into = _lookup_candidates(bst_index, bst_id)

        if merged_into is None:
            name = payload.get("name")
            if isinstance(name, str):
                merged_into = _lookup_candidates(name_index, name)

        if merged_into is not None:
            _merge_wl_payload(merged_into, payload)
            continue

        entry = dict(payload)
        entry["source"] = _merge_sources(payload.get("source"), "wl") or "wl"
        _ensure_sorted_aliases(entry)
        unmatched.append(entry)

    filtered.extend(unmatched)

    with stations_path.open("w", encoding="utf-8") as handle:
        if is_wrapped:
            json.dump({"stations": filtered}, handle, ensure_ascii=False, indent=2)
        else:
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

    vor_mapping = load_vor_mapping(args.vor_mapping)
    if vor_mapping:
        log.info("Loaded %d VOR mapping entries", len(vor_mapping))

    wl_entries = build_wl_entries(haltestellen, haltepunkte, vor_mapping)
    log.info("Prepared %d WL station entries", len(wl_entries))

    merge_into_stations(args.stations, wl_entries)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
