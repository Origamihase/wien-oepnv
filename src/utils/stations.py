"""Helpers for working with the ÖBB station directory."""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Iterable, NamedTuple

__all__ = [
    "canonical_name",
    "is_in_vienna",
    "is_pendler",
    "station_by_oebb_id",
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
    source: str | None = None

_STATIONS_PATH = Path(__file__).resolve().parents[2] / "data" / "stations.json"
_VIENNA_POLYGON_PATH = Path(__file__).resolve().parents[2] / "data" / "vienna_boundary.geojson"

Coordinate = tuple[float, float]
Ring = tuple[Coordinate, ...]
Polygon = tuple[Ring, ...]


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
    start: Coordinate,
    end: Coordinate,
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


def _point_in_ring(lat: float, lon: float, ring: Ring) -> bool:
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


def _point_in_polygon(lat: float, lon: float, rings: Polygon) -> bool:
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
def _vienna_polygons() -> tuple[Polygon, ...]:
    """Return a tuple of polygon rings representing Vienna's city limits."""

    try:
        with _VIENNA_POLYGON_PATH.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return ()

    polygons: list[Polygon] = []

    def add_polygon(coords: Iterable[Iterable[Iterable[float]]]) -> None:
        parsed_rings: list[Ring] = []
        for raw_ring in coords:
            ring_points: list[Coordinate] = []
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

    variants: list[str] = []
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

    result: list[dict] = []
    for entry in entries:
        if isinstance(entry, dict):
            result.append(entry)
    return tuple(result)


@lru_cache(maxsize=1)
def _station_lookup() -> dict[str, StationInfo]:
    """Return a mapping from normalized aliases to :class:`StationInfo` records."""

    mapping: dict[str, StationInfo] = {}

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
        source_text = str(entry.get("source") or "")
        base_record = StationInfo(
            name=name,
            in_vienna=bool(entry.get("in_vienna")),
            pendler=bool(entry.get("pendler")),
            wl_diva=wl_diva or None,
            wl_stops=tuple(stop_records),
            vor_id=vor_id or None,
            latitude=station_latitude,
            longitude=station_longitude,
            source=source_text or None,
        )
        vor_name_raw = entry.get("vor_name")
        vor_name = str(vor_name_raw).strip() if isinstance(vor_name_raw, str) else ""
        for alias in _iter_aliases(name, code or None, extra_aliases):
            alias_text = str(alias)
            alias_lower = alias_text.casefold()
            alias_is_numeric = alias_text.isdigit()
            alias_mentions_vor = "vor" in alias_lower
            alias_matches_vor_id = bool(vor_id and alias_is_numeric and alias_text == vor_id)
            use_vor_label = bool(vor_name and (alias_matches_vor_id or alias_mentions_vor))

            alias_record = base_record
            if use_vor_label:
                alias_record = StationInfo(
                    name=vor_name,
                    in_vienna=base_record.in_vienna,
                    pendler=base_record.pendler,
                    wl_diva=base_record.wl_diva,
                    wl_stops=base_record.wl_stops,
                    vor_id=base_record.vor_id,
                    latitude=base_record.latitude,
                    longitude=base_record.longitude,
                    source="vor",
                )

            key = _normalize_token(alias_text)
            if not key:
                continue
            existing = mapping.get(key)
            if existing is None:
                mapping[key] = alias_record
                continue
            if existing == alias_record or existing.name == alias_record.name:
                continue

            existing_source = existing.source or ""
            record_source = alias_record.source or ""
            if existing_source == "combined":
                existing_source = "wl"
            if record_source == "combined":
                record_source = "wl"

            alias_token = _normalize_token(alias_text)
            record_token = _normalize_token(alias_record.name)
            existing_token = _normalize_token(existing.name)

            if alias_token and alias_token == record_token and alias_token != existing_token:
                mapping[key] = alias_record
                continue
            if alias_token and alias_token == existing_token and alias_token != record_token:
                continue

            if existing.vor_id and alias_record.vor_id and existing.vor_id == alias_record.vor_id:
                if alias_is_numeric or alias_mentions_vor:
                    if record_source == "vor" and existing_source != "vor":
                        mapping[key] = alias_record
                else:
                    if record_source == "wl" and existing_source != "wl":
                        mapping[key] = alias_record
                continue

            if existing_source == "vor" and record_source != "vor":
                mapping[key] = alias_record
                continue
            if record_source == "vor" and existing_source != "vor":
                continue
            logger.warning(
                "Duplicate station alias %r normalized to %r for %s conflicts with %s",
                alias,
                key,
                alias_record.name,
                existing.name,
            )
    return mapping


def _candidate_values(value: str) -> list[str]:
    """Generate possible textual variants for *value* supplied by the caller."""

    candidates: list[str] = []
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
    extras: list[str] = []
    for variant in candidates:
        if re.search(r"\b(?:bei|b[./-]?)\s*wien\b", variant, re.IGNORECASE):
            stripped = re.sub(
                r"\b(?:bei|b[./-]?)\s*wien\b", "", variant, flags=re.IGNORECASE
            )
            extras.append(stripped)

        # Expand common station abbreviations like "Hbf"/"Bhf"/"Bf" to improve
        # canonical lookups for ÖBB titles that use shorthand spellings.
        if re.search(r"\bHbf\b", variant, re.IGNORECASE):
            extras.append(re.sub(r"\bHbf\b", "Hauptbahnhof", variant, flags=re.IGNORECASE))
        if re.search(r"\bBhf\b", variant, re.IGNORECASE):
            extras.append(re.sub(r"\bBhf\b", "Bahnhof", variant, flags=re.IGNORECASE))
        if re.search(r"\bBf\b", variant, re.IGNORECASE):
            extras.append(re.sub(r"\bBf\b", "Bahnhof", variant, flags=re.IGNORECASE))

    for extra in extras:
        cleaned = re.sub(r"\s{2,}", " ", extra.strip())
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            candidates.append(cleaned)
    return candidates


@lru_cache(maxsize=1024)
def station_by_oebb_id(bst_id: int | str) -> str | None:
    """Return the station name for a given ÖBB station ID (bst_id)."""
    try:
        target_id = int(bst_id)
    except (ValueError, TypeError):
        return None

    for entry in _station_entries():
        # bst_id in json is int
        current_id = entry.get("bst_id")
        if current_id == target_id:
            return entry.get("name")
    return None


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
    sorted tuple of distinct identifiers. Numeric aliases are also included to
    preserve legacy identifiers that may still be referenced externally. This
    centralizes the list of
    departure board locations that should be queried by the VOR provider and is
    used as a repository default when no explicit ``VOR_STATION_IDS``
    environment variable is configured.
    """

    ids: set[str] = set()
    for entry in _station_entries():
        if not (entry.get("in_vienna") or entry.get("pendler")):
            continue
        vor_id_raw = entry.get("vor_id")
        if vor_id_raw is not None:
            vor_id = str(vor_id_raw).strip()
            if vor_id:
                ids.add(vor_id)
        aliases_field = entry.get("aliases")
        if isinstance(aliases_field, list):
            for alias in aliases_field:
                if alias is None:
                    continue
                alias_text = str(alias).strip()
                if alias_text.isdigit():
                    ids.add(alias_text)
    return tuple(sorted(ids))
