"""Helpers for working with the ÖBB station directory."""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, NamedTuple, Sequence, Tuple

__all__ = [
    "canonical_name",
    "is_in_vienna",
    "is_pendler",
    "station_info",
    "vor_station_ids",
]


logger = logging.getLogger(__name__)


class WLStop(NamedTuple):
    """Coordinates for a Wiener Linien stop/platform."""

    stop_id: str
    name: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class StationInfo(NamedTuple):
    """Normalized metadata for a single station entry."""

    name: str
    in_vienna: bool
    pendler: bool
    wl_diva: str | None = None
    wl_stops: tuple[WLStop, ...] = ()
    vor_id: str | None = None
    latitude: float | None = None
    longitude: float | None = None

_STATIONS_PATH = Path(__file__).resolve().parents[2] / "data" / "stations.json"
_VOR_MAPPING_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "vor-haltestellen.mapping.json"
)
_VIENNA_POLYGON_PATH = Path(__file__).resolve().parents[2] / "data" / "vienna_boundary.geojson"


def _strip_accents(value: str) -> str:
    """Return *value* without diacritic marks."""

    return "".join(
        ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch)
    )


def _normalize_token(value: str) -> str:
    """Produce a canonical lookup token for a station alias."""

    if not value:
        return ""

    text = _strip_accents(value)
    text = text.replace("ß", "ss")
    text = text.casefold()
    text = re.sub(r"\ba\s*(?:[./]\s*)?d(?:[./]\s*)?\b", "an der ", text)
    text = text.replace("ae", "a").replace("oe", "o").replace("ue", "u")
    text = re.sub(r"\bst[. ]?\b", "sankt ", text)
    text = re.sub(r"\b(?:bahnhof|bahnhst|bhf|hbf|bf)\b", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def _coerce_float(value: object | None) -> float | None:
    """Return *value* as float if possible (accepting comma decimal separators)."""

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


def _point_on_segment(
    lat: float,
    lon: float,
    start: Tuple[float, float],
    end: Tuple[float, float],
    tolerance: float = 1e-9,
) -> bool:
    """Return ``True`` if *lat*, *lon* lies on the line segment (*start*, *end*)."""

    lat1, lon1 = start
    lat2, lon2 = end
    if abs(lat - lat1) <= tolerance and abs(lon - lon1) <= tolerance:
        return True
    if abs(lat - lat2) <= tolerance and abs(lon - lon2) <= tolerance:
        return True
    if not (
        min(lat1, lat2) - tolerance <= lat <= max(lat1, lat2) + tolerance
        and min(lon1, lon2) - tolerance <= lon <= max(lon1, lon2) + tolerance
    ):
        return False
    dx_segment = lon2 - lon1
    dy_segment = lat2 - lat1
    dx_point = lon - lon1
    dy_point = lat - lat1
    cross = dx_point * dy_segment - dy_point * dx_segment
    if abs(cross) > tolerance:
        return False
    dot = dx_point * dx_segment + dy_point * dy_segment
    length_sq = dx_segment * dx_segment + dy_segment * dy_segment
    return -tolerance <= dot <= length_sq + tolerance


def _point_in_ring(lat: float, lon: float, ring: Sequence[Tuple[float, float]]) -> bool:
    """Return ``True`` if *lat*, *lon* is inside the polygon *ring*."""

    if len(ring) < 3:
        return False
    inside = False
    for index in range(len(ring)):
        start = ring[index]
        end = ring[(index + 1) % len(ring)]
        if _point_on_segment(lat, lon, start, end):
            return True
        lat1, lon1 = start
        lat2, lon2 = end
        if (lat1 > lat) != (lat2 > lat):
            try:
                intersect_lon = lon1 + (lon2 - lon1) * (lat - lat1) / (lat2 - lat1)
            except ZeroDivisionError:
                intersect_lon = lon1
            if lon < intersect_lon:
                inside = not inside
    return inside


def _point_in_polygon(lat: float, lon: float, rings: Sequence[Sequence[Tuple[float, float]]]) -> bool:
    """Return ``True`` if *lat*, *lon* lies within the polygon defined by *rings*."""

    if not rings:
        return False
    if not _point_in_ring(lat, lon, rings[0]):
        return False
    for hole in rings[1:]:
        if _point_in_ring(lat, lon, hole):
            return False
    return True


@lru_cache(maxsize=1)
def _vienna_polygons() -> Tuple[Tuple[Tuple[float, float], ...], ...]:
    """Return a tuple of polygon rings representing Vienna's city limits."""

    try:
        with _VIENNA_POLYGON_PATH.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return ()

    polygons: list[tuple[tuple[float, float], ...]] = []

    def add_polygon(coords: Iterable[Iterable[Iterable[float]]]) -> None:
        parsed_rings: list[tuple[Tuple[float, float], ...]] = []
        for raw_ring in coords:
            ring_points: list[Tuple[float, float]] = []
            for pair in raw_ring:
                if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                    continue
                lon = _coerce_float(pair[0])
                lat = _coerce_float(pair[1])
                if lat is None or lon is None:
                    continue
                ring_points.append((lat, lon))
            if len(ring_points) >= 3:
                parsed_rings.append(tuple(ring_points))
        if parsed_rings:
            polygons.append(tuple(parsed_rings))

    def handle_geometry(payload: object) -> None:
        if not isinstance(payload, dict):
            return
        geometry_type = payload.get("type")
        coordinates = payload.get("coordinates")
        if geometry_type == "Polygon" and isinstance(coordinates, list):
            add_polygon(coordinates)
        elif geometry_type == "MultiPolygon" and isinstance(coordinates, list):
            for polygon in coordinates:
                if isinstance(polygon, list):
                    add_polygon(polygon)

    if isinstance(data, dict):
        data_type = data.get("type")
        if data_type == "FeatureCollection":
            features = data.get("features")
            if isinstance(features, list):
                for feature in features:
                    if isinstance(feature, dict):
                        handle_geometry(feature.get("geometry"))
        elif data_type == "Feature":
            handle_geometry(data.get("geometry"))
        else:
            handle_geometry(data)

    return tuple(polygons)


def _iter_aliases(
    name: str, code: str | None, extras: Iterable[str] | None = None
) -> Iterable[str]:
    """Yield alias strings for a station entry (canonical name first)."""

    variants: List[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        candidate = re.sub(r"\s{2,}", " ", raw.strip())
        if candidate and candidate not in seen:
            seen.add(candidate)
            variants.append(candidate)

    add(name)
    if code:
        add(code)

    no_paren = re.sub(r"\s*\([^)]*\)\s*", " ", name)
    add(no_paren)
    add(no_paren.replace("-", " "))
    add(no_paren.replace("/", " "))
    add(name.replace("-", " "))
    add(name.replace("/", " "))

    if re.search(r"\bSt\.?\b", name):
        add(re.sub(r"\bSt\.?\b", "St", name))
        add(re.sub(r"\bSt\.?\b", "St ", name))
        add(re.sub(r"\bSt\.?\b", "Sankt ", name))
    if re.search(r"\bSankt\b", name):
        add(re.sub(r"\bSankt\b", "St.", name))
        add(re.sub(r"\bSankt\b", "St ", name))
        add(re.sub(r"\bSankt\b", "St", name))

    if extras:
        for extra in extras:
            add(str(extra))

    return variants


@lru_cache(maxsize=1)
def _station_entries() -> tuple[dict, ...]:
    """Return the raw station entries from :mod:`data/stations.json`."""

    try:
        with _STATIONS_PATH.open("r", encoding="utf-8") as handle:
            entries = json.load(handle)
    except (OSError, json.JSONDecodeError):  # pragma: no cover - defensive
        return ()

    if not isinstance(entries, list):
        return ()

    vor_overrides = _vor_overrides()
    additional_vor_entries = _vor_additional_entries()
    result: list[dict] = []
    for entry in entries:
        if isinstance(entry, dict):
            merged = dict(entry)
            bst_id = merged.get("bst_id")
            bst_key: int | None
            if isinstance(bst_id, int):
                bst_key = bst_id
            else:
                try:
                    bst_key = int(str(bst_id))
                except (TypeError, ValueError):
                    bst_key = None
            if bst_key is not None and bst_key in vor_overrides:
                override = vor_overrides[bst_key]
                alias_values: list[str] = []
                existing_aliases = merged.get("aliases")
                if isinstance(existing_aliases, list):
                    alias_values.extend(str(item) for item in existing_aliases if item is not None)
                override_aliases = override.get("aliases")
                if isinstance(override_aliases, list):
                    alias_values.extend(str(item) for item in override_aliases if item is not None)
                cleaned_aliases = sorted({alias.strip() for alias in alias_values if alias and alias.strip()})
                if cleaned_aliases:
                    merged["aliases"] = cleaned_aliases
                elif "aliases" in merged:
                    merged.pop("aliases")
                for key in ("vor_id", "latitude", "longitude"):
                    if key in override:
                        merged[key] = override[key]
            result.append(merged)
    for extra in additional_vor_entries:
        if isinstance(extra, dict):
            result.append(dict(extra))
    return tuple(result)


@lru_cache(maxsize=1)
def _vor_mapping_entries() -> tuple[dict[str, object], ...]:
    try:
        with _VOR_MAPPING_PATH.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):  # pragma: no cover - defensive
        return ()

    if not isinstance(payload, list):
        return ()

    entries: list[dict[str, object]] = []
    for entry in payload:
        if isinstance(entry, dict):
            entries.append(dict(entry))
    return tuple(entries)


@lru_cache(maxsize=1)
def _vor_overrides() -> dict[int, dict[str, object]]:
    """Return VOR-specific metadata keyed by ``bst_id``.

    The information is maintained separately to avoid merge conflicts when
    updating the ÖBB station directory. Each entry may provide a ``vor_id``,
    coordinates and additional aliases derived from the VAO name resolution.
    """

    overrides: dict[int, dict[str, object]] = {}
    for entry in _vor_mapping_entries():
        bst_raw = entry.get("bst_id")
        try:
            bst_id = int(str(bst_raw))
        except (TypeError, ValueError):
            continue
        vor_id_raw = entry.get("vor_id")
        vor_id = str(vor_id_raw).strip() if vor_id_raw is not None else ""
        if not vor_id:
            continue
        alias_candidates: set[str] = {vor_id}
        resolved_name_raw = entry.get("resolved_name")
        resolved_name = str(resolved_name_raw).strip() if resolved_name_raw is not None else ""
        if resolved_name:
            alias_candidates.add(resolved_name)
        override: dict[str, object] = {"vor_id": vor_id}
        latitude = _coerce_float(entry.get("latitude"))
        longitude = _coerce_float(entry.get("longitude"))
        if latitude is not None:
            override["latitude"] = latitude
        if longitude is not None:
            override["longitude"] = longitude
        aliases = sorted(alias for alias in alias_candidates if alias)
        if aliases:
            override["aliases"] = aliases
        overrides[bst_id] = override
    return overrides


@lru_cache(maxsize=1)
def _vor_additional_entries() -> tuple[dict[str, object], ...]:
    entries: list[dict[str, object]] = []
    polygons = _vienna_polygons()
    for entry in _vor_mapping_entries():
        bst_raw = entry.get("bst_id")
        try:
            bst_id = int(str(bst_raw))
        except (TypeError, ValueError):
            bst_id = None
        if bst_id is not None:
            continue
        vor_id_raw = entry.get("vor_id")
        vor_id = str(vor_id_raw).strip() if vor_id_raw is not None else ""
        if not vor_id:
            continue
        resolved_name_raw = entry.get("resolved_name")
        station_name_raw = entry.get("station_name")
        resolved_name = (
            str(resolved_name_raw).strip() if resolved_name_raw is not None else ""
        )
        station_name = (
            str(station_name_raw).strip() if station_name_raw is not None else ""
        )
        name = resolved_name or station_name or vor_id
        latitude = _coerce_float(entry.get("latitude"))
        longitude = _coerce_float(entry.get("longitude"))
        in_vienna = False
        if latitude is not None and longitude is not None:
            for polygon in polygons:
                if _point_in_polygon(latitude, longitude, polygon):
                    in_vienna = True
                    break
        alias_candidates = {
            alias.strip()
            for alias in {vor_id, resolved_name, station_name}
            if alias and alias.strip()
        }
        entries.append(
            {
                "name": name,
                "in_vienna": in_vienna,
                "pendler": False,
                "vor_id": vor_id,
                "latitude": latitude,
                "longitude": longitude,
                "aliases": sorted(alias_candidates),
                "source": "vor",
            }
        )
    return tuple(entries)


@lru_cache(maxsize=1)
def _station_lookup() -> Dict[str, StationInfo]:
    """Return a mapping from normalized aliases to :class:`StationInfo` records."""

    mapping: Dict[str, StationInfo] = {}

    for entry in _station_entries():
        name = str(entry.get("name", "")).strip()
        if not name:
            continue
        code_raw = entry.get("bst_code")
        code = str(code_raw).strip() if code_raw is not None else ""
        wl_diva_raw = entry.get("wl_diva")
        wl_diva = str(wl_diva_raw).strip() if wl_diva_raw is not None else ""
        vor_id_raw = entry.get("vor_id")
        vor_id = str(vor_id_raw).strip() if vor_id_raw is not None else ""
        extra_aliases: set[str] = set()
        if wl_diva:
            extra_aliases.add(wl_diva)
        if vor_id:
            extra_aliases.add(vor_id)
        aliases_field = entry.get("aliases")
        if isinstance(aliases_field, list):
            for alias in aliases_field:
                if alias is None:
                    continue
                alias_text = str(alias).strip()
                if alias_text:
                    extra_aliases.add(alias_text)
        stop_records: list[WLStop] = []
        stops_field = entry.get("wl_stops")
        if isinstance(stops_field, list):
            for stop in stops_field:
                if not isinstance(stop, dict):
                    continue
                stop_id_raw = stop.get("stop_id")
                stop_id = str(stop_id_raw).strip() if stop_id_raw is not None else ""
                name_raw = stop.get("name")
                stop_name = str(name_raw).strip() if name_raw is not None else ""
                latitude = _coerce_float(stop.get("latitude"))
                longitude = _coerce_float(stop.get("longitude"))
                if stop_id:
                    extra_aliases.add(stop_id)
                if stop_name:
                    extra_aliases.add(stop_name)
                stop_records.append(
                    WLStop(
                        stop_id=stop_id,
                        name=stop_name or None,
                        latitude=latitude,
                        longitude=longitude,
                    )
                )
        station_latitude = _coerce_float(entry.get("latitude"))
        station_longitude = _coerce_float(entry.get("longitude"))
        record = StationInfo(
            name=name,
            in_vienna=bool(entry.get("in_vienna")),
            pendler=bool(entry.get("pendler")),
            wl_diva=wl_diva or None,
            wl_stops=tuple(stop_records),
            vor_id=vor_id or None,
            latitude=station_latitude,
            longitude=station_longitude,
        )
        for alias in _iter_aliases(name, code or None, extra_aliases):
            key = _normalize_token(alias)
            if not key:
                continue
            existing = mapping.get(key)
            if existing is None:
                mapping[key] = record
                continue
            if existing is record or existing.name == record.name:
                continue
            logger.warning(
                "Duplicate station alias %r normalized to %r for %s conflicts with %s",
                alias,
                key,
                record.name,
                existing.name,
            )
    return mapping


def _candidate_values(value: str) -> List[str]:
    """Generate possible textual variants for *value* supplied by the caller."""

    candidates: List[str] = []
    seen: set[str] = set()
    for variant in (
        value,
        value.strip(),
        re.sub(r"\s*\([^)]*\)\s*", " ", value),
        value.replace("-", " "),
        value.replace("/", " "),
    ):
        cleaned = re.sub(r"\s{2,}", " ", variant.strip())
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            candidates.append(cleaned)
    extras: List[str] = []
    for variant in candidates:
        if re.search(r"\b(?:bei|b[./-]?)\s*wien\b", variant, re.IGNORECASE):
            stripped = re.sub(r"\b(?:bei|b[./-]?)\s*wien\b", "", variant, flags=re.IGNORECASE)
            extras.append(stripped)
    for extra in extras:
        cleaned = re.sub(r"\s{2,}", " ", extra.strip())
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            candidates.append(cleaned)
    return candidates


@lru_cache(maxsize=2048)
def canonical_name(name: str) -> str | None:
    """Return the canonical ÖBB station name for *name* or ``None`` if unknown."""

    info = station_info(name)
    return info.name if info else None


@lru_cache(maxsize=2048)
def station_info(name: str) -> StationInfo | None:
    """Return :class:`StationInfo` for *name* or ``None`` if the station is unknown."""

    if not isinstance(name, str):  # pragma: no cover - defensive
        return None

    lookup = _station_lookup()
    if not lookup:
        return None

    for candidate in _candidate_values(name):
        key = _normalize_token(candidate)
        if not key:
            continue
        info = lookup.get(key)
        if info:
            return info
    return None


def is_in_vienna(lat: object, lon: object | None = None) -> bool:
    """Return ``True`` if the supplied coordinates or station name lie in Vienna."""

    if lon is None and isinstance(lat, str):
        info = station_info(lat)
        if info:
            return bool(info.in_vienna)
        token = _normalize_token(lat)
        if token == "wien" or token.startswith("wien "):
            return True
        return False

    latitude = _coerce_float(lat)
    longitude = _coerce_float(lon)
    if latitude is None or longitude is None:
        return False

    for polygon in _vienna_polygons():
        if _point_in_polygon(latitude, longitude, polygon):
            return True
    return False


def is_pendler(name: str) -> bool:
    """Return ``True`` if *name* is part of the configured commuter belt."""

    info = station_info(name)
    return bool(info and info.pendler)


@lru_cache(maxsize=1)
def vor_station_ids() -> tuple[str, ...]:
    """Return the configured VOR station IDs from ``stations.json``.

    The function collects all entries that provide a ``vor_id`` and returns a
    sorted tuple of distinct identifiers. This centralizes the list of
    departure board locations that should be queried by the VOR provider and is
    used as a repository default when no explicit ``VOR_STATION_IDS``
    environment variable is configured.
    """

    ids: set[str] = set()
    for entry in _station_entries():
        vor_id_raw = entry.get("vor_id")
        if vor_id_raw is None:
            continue
        vor_id = str(vor_id_raw).strip()
        if not vor_id:
            continue
        ids.add(vor_id)
    return tuple(sorted(ids))
