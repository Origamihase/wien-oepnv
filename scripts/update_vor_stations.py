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
from itertools import count
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Sequence

import requests

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:  # pragma: no cover - convenience for module execution
    from src.providers import vor as vor_provider
    from src.utils.http import session_with_retries
    from src.utils.stations import is_in_vienna, is_pendler
except ModuleNotFoundError:  # pragma: no cover - fallback when installed as package
    from providers import vor as vor_provider  # type: ignore
    from utils.http import session_with_retries  # type: ignore
    from utils.stations import is_in_vienna, is_pendler  # type: ignore
DEFAULT_SOURCE = BASE_DIR / "data" / "vor-haltestellen.csv"
DEFAULT_STATIONS = BASE_DIR / "data" / "stations.json"

log = logging.getLogger("update_vor_stations")

STATIC_VOR_ENTRIES: tuple[dict[str, object], ...] = (
    {
        "vor_id": "900300",
        "name": "Wiener Neustadt Hbf",
        "in_vienna": False,
        "pendler": True,
        "latitude": 47.811304,
        "longitude": 16.23362,
        "aliases": [
            "Wiener Neustadt Hauptbahnhof",
            "Wiener Neustadt Hauptbahnhof (VOR)",
            "Wiener Neustadt Hbf",
            "Wiener Neustadt",
            "Wiener Neustadt Bahnhof",
            "Bahnhof Wiener Neustadt",
            "Wr. Neustadt Hbf",
            "Wr. Neustadt",
            "900300",
            "430521000",
        ],
        "bst_id": "900300",
        "bst_code": "900300",
        "source": "vor",
    },
    {
        "vor_id": "490091000",
        "name": "Wien Aspern Nord",
        "in_vienna": True,
        "pendler": False,
        "latitude": 48.234567,
        "longitude": 16.520123,
        "aliases": [
            "Wien Aspern Nord",
            "Aspern Nord",
            "900100",
            "490091000",
        ],
        "bst_id": "900100",
        "bst_code": "900100",
        "source": "vor",
    },
)


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
        "--use-api",
        action="store_true",
        help="Fetch stop metadata from the VOR API instead of relying solely on CSV data",
    )
    parser.add_argument(
        "--station-id",
        dest="station_ids",
        action="append",
        default=[],
        help="Additional VOR station ID to fetch when --use-api is supplied (can be repeated)",
    )
    parser.add_argument(
        "--station-id-file",
        type=Path,
        help="Optional file with one VOR station ID per line for --use-api",
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


def _read_station_ids_from_file(path: Path) -> list[str]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:  # pragma: no cover - defensive
        log.warning("Could not read station ID file %s: %s", path, exc)
        return []
    ids: list[str] = []
    for segment in raw.replace(",", "\n").splitlines():
        text = segment.strip()
        if text and text not in ids:
            ids.append(text)
    return ids


def _build_property_map(data: Mapping[str, object]) -> dict[str, str]:
    props: dict[str, str] = {}
    raw = data.get("properties")
    if isinstance(raw, Mapping):
        raw_items = raw.get("property") or raw.get("properties")
        if isinstance(raw_items, list):
            candidates = raw_items
        else:
            candidates = [raw_items] if raw_items else []
    else:
        candidates = raw if isinstance(raw, list) else []
    for item in candidates:
        if not isinstance(item, Mapping):
            continue
        key = str(item.get("name") or item.get("key") or item.get("type") or "").strip()
        value = item.get("value") or item.get("valueString") or item.get("val")
        if not key or value is None:
            continue
        text = str(value).strip()
        if text:
            props[key.casefold()] = text
    return props


def _extract_from_mapping(data: Mapping[str, object], *candidates: str) -> str:
    for candidate in candidates:
        value = data.get(candidate)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _extract_from_properties(props: Mapping[str, str], *candidates: str) -> str:
    for candidate in candidates:
        key = candidate.casefold()
        value = props.get(key)
        if value:
            return value
    return ""


def _extract_coordinate(data: Mapping[str, object], axis: str) -> float | None:
    axis_lower = axis.casefold()
    candidates: list[object] = []
    coord = data.get("coord")
    if isinstance(coord, Mapping):
        candidates.extend(
            coord.get(key)
            for key in (axis_lower, axis_lower[:1], axis_lower.upper(), axis.title())
        )
        candidates.extend(coord.get(key) for key in (f"{axis_lower}itude", f"{axis_lower}Coord"))
    candidates.extend(
        data.get(key)
        for key in (
            axis_lower,
            axis_lower[:1],
            axis_lower.upper(),
            axis.title(),
            f"{axis_lower}itude",
            f"{axis_lower}Coord",
            f"geo{axis.title()}",
            f"{axis_lower}_wgs84",
        )
    )
    props = _build_property_map(data)
    candidates.extend(props.get(key) for key in (axis_lower, f"{axis_lower}itude"))
    for candidate in candidates:
        if candidate is None:
            continue
        text = str(candidate).strip().replace(",", ".")
        if not text:
            continue
        try:
            return float(text)
        except ValueError:
            continue
    return None


def _parse_api_stop(data: Mapping[str, object], wanted_id: str | None = None) -> VORStop | None:
    props = _build_property_map(data)
    vor_id = _extract_from_mapping(
        data,
        "id",
        "extId",
        "stopId",
        "stop_id",
        "StopPointId",
        "StopID",
    )
    if not vor_id:
        vor_id = _extract_from_properties(
            props,
            "id",
            "extid",
            "stopid",
            "stop_id",
            "stoppointid",
        )
    if not vor_id:
        return None
    vor_id = vor_id.strip()
    if wanted_id and vor_id != wanted_id:
        # some APIs may prefix zeros or provide related stops; prefer exact matches
        if vor_id.lstrip("0") != wanted_id.lstrip("0"):
            return None
        vor_id = wanted_id

    name = _extract_from_mapping(
        data,
        "name",
        "StopPointName",
        "stopPointName",
        "value",
    )
    if not name:
        name = _extract_from_properties(props, "name", "stoppointname")
    if not name:
        return None

    municipality = _extract_from_mapping(
        data,
        "municipality",
        "place",
        "city",
        "ort",
    )
    if not municipality:
        municipality = _extract_from_properties(
            props,
            "municipality",
            "place",
            "city",
            "ort",
        )

    short_name = _extract_from_mapping(data, "shortName", "shortname", "StopPointShortName")
    if not short_name:
        short_name = _extract_from_properties(props, "shortname", "stoppointshortname")

    global_id = _extract_from_mapping(data, "globalId", "globalID", "StopPointGlobalId")
    if not global_id:
        global_id = _extract_from_properties(
            props,
            "globalid",
            "stoppointglobalid",
            "gid",
        )

    gtfs_stop_id = _extract_from_mapping(data, "gtfsStopId", "gtfs_stop_id")
    if not gtfs_stop_id:
        gtfs_stop_id = _extract_from_properties(props, "gtfsstopid", "gtfs_stop_id", "stopid")

    latitude = _extract_coordinate(data, "lat")
    longitude = _extract_coordinate(data, "lon")

    return VORStop(
        vor_id=vor_id,
        name=name,
        latitude=latitude,
        longitude=longitude,
        municipality=municipality or None,
        short_name=short_name or None,
        global_id=global_id or None,
        gtfs_stop_id=gtfs_stop_id or None,
    )


def fetch_vor_stops_from_api(
    station_ids: Iterable[str],
    fallback: Mapping[str, VORStop] | None = None,
) -> list[VORStop]:
    ids = [str(station_id).strip() for station_id in station_ids if str(station_id).strip()]
    if not ids:
        return []

    fallback_map: dict[str, VORStop] = {}
    if fallback:
        fallback_map = {key: value for key, value in fallback.items()}

    stops: list[VORStop] = []
    with session_with_retries(vor_provider.VOR_USER_AGENT, **vor_provider.VOR_RETRY_OPTIONS) as session:
        vor_provider.apply_authentication(session)
        for station_id in ids:
            params = {"format": "json", "input": station_id, "type": "stop", "maxNo": 8}
            try:
                response = session.get(
                    f"{vor_provider.VOR_BASE_URL}location.name",
                    params=params,
                    timeout=vor_provider.HTTP_TIMEOUT,
                    headers={"Accept": "application/json"},
                )
            except requests.RequestException as exc:
                log.warning("VOR API request for %s failed: %s", station_id, exc)
                stop = fallback_map.get(station_id)
                if stop:
                    stops.append(stop)
                continue

            if response.status_code >= 400:
                log.warning(
                    "VOR API returned HTTP %s for station %s", response.status_code, station_id
                )
                stop = fallback_map.get(station_id)
                if stop:
                    stops.append(stop)
                continue

            try:
                payload = response.json()
            except ValueError:
                log.warning("VOR API returned invalid JSON for station %s", station_id)
                stop = fallback_map.get(station_id)
                if stop:
                    stops.append(stop)
                continue

            raw_stops = payload.get("StopLocation")
            if isinstance(raw_stops, Mapping):
                candidates = [raw_stops]
            elif isinstance(raw_stops, list):
                candidates = [item for item in raw_stops if isinstance(item, Mapping)]
            else:
                candidates = []

            parsed_stop: VORStop | None = None
            for candidate in candidates:
                parsed_stop = _parse_api_stop(candidate, wanted_id=station_id)
                if parsed_stop:
                    break

            if parsed_stop is None and candidates:
                # fall back to the first candidate even if the ID mismatched to avoid data loss
                parsed_stop = _parse_api_stop(candidates[0])

            if parsed_stop is None:
                stop = fallback_map.get(station_id)
                if stop:
                    stops.append(stop)
                else:
                    log.info("VOR API did not return usable data for station %s", station_id)
                continue

            stops.append(parsed_stop)

    return stops


def _looks_like_vienna(text: str | None) -> bool:
    if not text:
        return False
    normalized = text.strip().casefold()
    if not normalized.startswith("wien"):
        return False
    if len(normalized) == 4:
        return True
    return not normalized[4].isalpha()


_SUFFIX_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\s+U\s*(?:\((?:VOR|WL)\))?$", flags=re.IGNORECASE),
    re.compile(r"\s+\((?:VOR|WL)\)$", flags=re.IGNORECASE),
)


def _strip_vor_suffixes(name: str) -> str:
    text = name
    for pattern in _SUFFIX_PATTERNS:
        text = pattern.sub("", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip(" -")


def _canonical_vor_name(name: str) -> str:
    cleaned = re.sub(r"\s{2,}", " ", name.strip())
    if not cleaned:
        return name.strip()

    mapped = (
        vor_provider.STATION_NAME_MAP.get(cleaned)
        or vor_provider.STATION_NAME_MAP.get(name.strip())
    )
    if mapped:
        cleaned = str(mapped).strip()

    stripped = _strip_vor_suffixes(cleaned)
    if stripped:
        cleaned = stripped

    if cleaned.casefold().startswith("vienna "):
        cleaned = f"Wien {cleaned[7:].lstrip()}".strip()

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
            if in_vienna and not (
                _looks_like_vienna(stop.municipality) or _looks_like_vienna(stop.name)
            ):
                log.debug(
                    "Overriding Vienna flag for VOR stop %s (%s) based on municipality",
                    stop.name,
                    stop.vor_id,
                )
                in_vienna = False
        else:
            in_vienna = _looks_like_vienna(stop.municipality) or _looks_like_vienna(stop.name)
            log.warning(
                "Missing coordinates for VOR stop %s (%s); falling back to heuristics",
                stop.name,
                stop.vor_id,
            )
        pendler = False
        if not in_vienna:
            pendler = bool(
                is_pendler(stop.name)
                or is_pendler(canonical)
                or (stop.short_name and is_pendler(stop.short_name))
            )

        entry = {
            "name": canonical,
            "in_vienna": in_vienna,
            "pendler": pendler,
            "vor_id": stop.vor_id,
            "latitude": stop.latitude,
            "longitude": stop.longitude,
            "aliases": _build_aliases(stop),
            "source": "vor",
        }
        entries.append(entry)
    entries.sort(key=lambda item: (str(item.get("name")), str(item.get("vor_id"))))
    return entries


def _collect_aliases(entry: Mapping[str, object]) -> list[str]:
    """Return all textual aliases defined for *entry* without duplicates."""

    aliases: list[str] = []
    seen: set[str] = set()

    def add(value: object | None) -> None:
        if value is None:
            return
        text = str(value).strip()
        if not text or text in seen:
            return
        seen.add(text)
        aliases.append(text)

    raw_aliases = entry.get("aliases")
    if isinstance(raw_aliases, list):
        for alias in raw_aliases:
            add(alias)

    add(entry.get("name"))
    add(entry.get("bst_code"))
    add(entry.get("vor_id"))
    add(entry.get("wl_diva"))

    return aliases


def merge_into_stations(stations_path: Path, vor_entries: list[dict[str, object]]) -> None:
    try:
        with stations_path.open("r", encoding="utf-8") as handle:
            existing_raw = json.load(handle)
    except FileNotFoundError:
        existing_raw = []

    if isinstance(existing_raw, dict):
        existing_raw = existing_raw.get("stations", [])

    if not isinstance(existing_raw, list):
        raise ValueError("stations.json must contain a JSON array")

    def _normalize_id(value: object | None) -> str:
        if isinstance(value, (int, float)):
            return str(int(value))
        if isinstance(value, str):
            return value.strip()
        return ""

    used_bst_ids: set[str] = set()
    used_bst_codes: set[str] = set()
    vor_id_to_entry: dict[str, dict[str, object]] = {}
    alias_to_entry: dict[str, dict[str, object]] = {}
    existing: list[dict[str, object]] = []

    for entry in existing_raw:
        if not isinstance(entry, dict):
            continue
        existing.append(entry)
        bst_id = _normalize_id(entry.get("bst_id"))
        if bst_id:
            used_bst_ids.add(bst_id)
        bst_code = _normalize_id(entry.get("bst_code"))
        if bst_code:
            used_bst_codes.add(bst_code)
        vor_id = _normalize_id(entry.get("vor_id"))
        if vor_id:
            vor_id_to_entry[vor_id] = entry
        for alias in _collect_aliases(entry):
            text = str(alias).strip()
            if text and text not in alias_to_entry:
                alias_to_entry[text] = entry

    static_map = {
        _normalize_id(static.get("vor_id")): dict(static)
        for static in STATIC_VOR_ENTRIES
        if _normalize_id(static.get("vor_id"))
    }
    handled_static: set[str] = set()

    def _apply_static_overrides(target: dict[str, object], static_entry: dict[str, object]) -> None:
        aliases = target.get("aliases")
        if not isinstance(aliases, list):
            aliases = []
        existing_aliases = {str(item).strip() for item in aliases if item}
        for alias in static_entry.get("aliases", []) if isinstance(static_entry.get("aliases"), list) else []:
            text = str(alias).strip()
            if text and text not in existing_aliases:
                aliases.append(text)
                existing_aliases.add(text)
        target["aliases"] = aliases
        for key in ("bst_id", "bst_code", "source", "name", "in_vienna", "pendler", "latitude", "longitude"):
            value = static_entry.get(key)
            if value is None:
                continue
            if key in {"bst_id", "bst_code"}:
                target[key] = _normalize_id(value)
            elif key not in target or target[key] in (None, ""):
                target[key] = value

    next_suffix = count(100)

    def _allocate_identifier() -> str:
        while True:
            candidate = f"900{next(next_suffix):03d}"
            if candidate in used_bst_ids or candidate in used_bst_codes:
                continue
            used_bst_ids.add(candidate)
            used_bst_codes.add(candidate)
            return candidate

    new_vor_entries: list[dict[str, object]] = []
    seen_vor_ids: set[str] = set()

    for vor_entry in vor_entries:
        if not isinstance(vor_entry, dict):
            continue
        vor_id = _normalize_id(vor_entry.get("vor_id"))
        if not vor_id:
            continue
        seen_vor_ids.add(vor_id)
        static_override = static_map.get(vor_id)
        if static_override is not None:
            handled_static.add(vor_id)
            _apply_static_overrides(vor_entry, static_override)

        target = vor_id_to_entry.get(vor_id)
        if target is None:
            for alias in _collect_aliases(vor_entry):
                text = str(alias).strip()
                target = alias_to_entry.get(text)
                if target is not None:
                    break

        if target is not None:
            for key in ("latitude", "longitude"):
                current = target.get(key)
                vor_value = vor_entry.get(key)
                if current in (None, "") and vor_value not in (None, ""):
                    target[key] = vor_value
            vor_aliases = target.get("aliases")
            if not isinstance(vor_aliases, list):
                vor_aliases = []
            existing_aliases = {str(item).strip() for item in vor_aliases if item}
            for alias in vor_entry.get("aliases", []) if isinstance(vor_entry.get("aliases"), list) else []:
                text = str(alias).strip()
                if text and text not in existing_aliases:
                    vor_aliases.append(text)
                    existing_aliases.add(text)
            target["aliases"] = vor_aliases
            if not _normalize_id(target.get("vor_id")):
                target["vor_id"] = vor_id
            continue

        new_entry = dict(vor_entry)
        bst_id = _normalize_id(new_entry.get("bst_id"))
        if not bst_id:
            bst_id = _allocate_identifier()
        new_entry["bst_id"] = bst_id
        used_bst_ids.add(bst_id)
        bst_code = _normalize_id(new_entry.get("bst_code"))
        if not bst_code:
            bst_code = bst_id
        new_entry["bst_code"] = bst_code
        used_bst_codes.add(bst_code)
        new_entry["source"] = "vor"
        new_vor_entries.append(new_entry)

    for vor_id, static_entry in static_map.items():
        if vor_id in handled_static:
            continue
        if vor_id in seen_vor_ids:
            continue
        if vor_id in vor_id_to_entry:
            continue
        new_entry = dict(static_entry)
        bst_id = _normalize_id(new_entry.get("bst_id")) or _allocate_identifier()
        new_entry["bst_id"] = bst_id
        used_bst_ids.add(bst_id)
        bst_code = _normalize_id(new_entry.get("bst_code")) or bst_id
        new_entry["bst_code"] = bst_code
        used_bst_codes.add(bst_code)
        aliases = new_entry.get("aliases")
        if isinstance(aliases, list):
            unique_aliases: list[str] = []
            seen_aliases: set[str] = set()
            for alias in aliases:
                text = str(alias).strip()
                if text and text not in seen_aliases:
                    unique_aliases.append(text)
                    seen_aliases.add(text)
            new_entry["aliases"] = unique_aliases
        else:
            new_entry["aliases"] = []
        new_entry.setdefault("source", "vor")
        new_vor_entries.append(new_entry)

    new_vor_entries.sort(key=lambda item: (str(item.get("name")), str(item.get("vor_id"))))
    merged_entries = existing + new_vor_entries

    with stations_path.open("w", encoding="utf-8") as handle:
        json.dump(
            {"stations": merged_entries}, handle, ensure_ascii=False, indent=2
        )
        handle.write("\n")
    log.info(
        "Wrote %d total stations (%d merged, %d added VOR entries)",
        len(merged_entries),
        sum(1 for entry in existing if _normalize_id(entry.get("vor_id")) in seen_vor_ids),
        len(new_vor_entries),
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(args.verbose)

    fallback_stops: list[VORStop] = []
    fallback_map: dict[str, VORStop] = {}

    if args.use_api:
        station_ids: list[str] = []
        if args.station_id_file:
            station_ids.extend(_read_station_ids_from_file(args.station_id_file))
        if args.station_ids:
            for raw in args.station_ids:
                text = (raw or "").strip()
                if text and text not in station_ids:
                    station_ids.append(text)

        if args.source:
            try:
                fallback_stops = load_vor_stops(args.source)
            except FileNotFoundError:
                log.info("CSV source %s not found – continuing without fallback data", args.source)
                fallback_stops = []
            fallback_map = {stop.vor_id: stop for stop in fallback_stops}
            if not station_ids and fallback_stops:
                station_ids = [stop.vor_id for stop in fallback_stops]

        if not station_ids:
            log.error(
                "No station IDs available for API import. Provide --station-id/--station-id-file or a CSV source."
            )
            return 1

        if not vor_provider.VOR_ACCESS_ID:
            log.error(
                "VOR_ACCESS_ID (or VAO_ACCESS_ID) must be configured when --use-api is supplied."
            )
            return 1

        log.info("Fetching %d VOR stops via API", len(station_ids))
        vor_stops = fetch_vor_stops_from_api(station_ids, fallback=fallback_map)
        log.info("Fetched %d VOR stops via API", len(vor_stops))
    else:
        if not args.source.exists():
            log.error("CSV source %s not found", args.source)
            return 1
        log.info("Reading VOR stops: %s", args.source)
        vor_stops = load_vor_stops(args.source)
        log.info("Found %d VOR stops", len(vor_stops))

    vor_entries = build_vor_entries(vor_stops)
    log.info("Prepared %d VOR station entries", len(vor_entries))

    merge_into_stations(args.stations, vor_entries)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
