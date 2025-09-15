"""Helpers for working with the ÖBB station directory."""

from __future__ import annotations

import json
import math
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, NamedTuple, Sequence, Tuple

__all__ = [
    "canonical_name",
    "is_in_vienna",
    "is_pendler",
    "is_station_in_vienna",
    "station_info",
]


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

_STATIONS_PATH = Path(__file__).resolve().parents[2] / "data" / "stations.json"
_VIENNA_BOUNDARY_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "vienna_boundary.geojson"
)

_Ring = Tuple[Tuple[float, float], ...]
_Polygon = Tuple[_Ring, Tuple[_Ring, ...]]


def _normalize_boundary_ring(raw: Sequence[Sequence[object]]) -> _Ring:
    """Return a normalized linear ring from raw GeoJSON coordinates."""

    coords: List[Tuple[float, float]] = []
    for point in raw:
        if not isinstance(point, Sequence) or len(point) < 2:
            continue
        lon, lat = point[0], point[1]
        try:
            x = float(lon)
            y = float(lat)
        except (TypeError, ValueError):
            continue
        coords.append((x, y))
    if len(coords) < 3:
        return ()
    if coords[0] != coords[-1]:
        coords.append(coords[0])
    return tuple(coords)


def _iter_boundary_polygons(payload: object) -> Iterable[Sequence[Sequence[Sequence[object]]]]:
    """Yield polygon coordinate arrays from a GeoJSON payload."""

    if not isinstance(payload, dict):
        return
    gtype = payload.get("type")
    if gtype == "FeatureCollection":
        features = payload.get("features")
        if isinstance(features, list):
            for feature in features:
                yield from _iter_boundary_polygons(feature)
        return
    if gtype == "Feature":
        yield from _iter_boundary_polygons(payload.get("geometry"))
        return
    if gtype == "MultiPolygon":
        polygons = payload.get("coordinates")
        if isinstance(polygons, list):
            for polygon in polygons:
                yield polygon
        return
    if gtype == "Polygon":
        coords = payload.get("coordinates")
        if isinstance(coords, list):
            yield coords


@lru_cache(maxsize=1)
def _vienna_polygons() -> Tuple[_Polygon, ...]:
    """Return simplified Vienna boundary polygons from the GeoJSON file."""

    try:
        with _VIENNA_BOUNDARY_PATH.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):  # pragma: no cover - defensive
        return ()

    polygons: List[_Polygon] = []
    for polygon in _iter_boundary_polygons(payload):
        if not polygon:
            continue
        outer_raw = polygon[0]
        outer = _normalize_boundary_ring(outer_raw) if isinstance(outer_raw, Sequence) else ()
        if not outer:
            continue
        holes: List[_Ring] = []
        for ring in polygon[1:]:
            if not isinstance(ring, Sequence):
                continue
            normalized = _normalize_boundary_ring(ring)
            if normalized:
                holes.append(normalized)
        polygons.append((outer, tuple(holes)))
    return tuple(polygons)


def _point_on_segment(
    x: float, y: float, x1: float, y1: float, x2: float, y2: float, *, eps: float = 1e-9
) -> bool:
    """Return ``True`` if the point lies on the given line segment."""

    if not (
        min(x1, x2) - eps <= x <= max(x1, x2) + eps
        and min(y1, y2) - eps <= y <= max(y1, y2) + eps
    ):
        return False
    dx = x2 - x1
    dy = y2 - y1
    if abs(dx) <= eps and abs(dy) <= eps:
        return abs(x - x1) <= eps and abs(y - y1) <= eps
    cross = (x - x1) * dy - (y - y1) * dx
    if abs(cross) > eps * max(abs(dx), abs(dy), 1.0):
        return False
    return True


def _point_in_ring(lat: float, lon: float, ring: _Ring) -> bool:
    """Return ``True`` if the point lies inside the closed linear *ring*."""

    x = lon
    y = lat
    inside = False
    for index in range(len(ring) - 1):
        x1, y1 = ring[index]
        x2, y2 = ring[index + 1]
        if _point_on_segment(x, y, x1, y1, x2, y2):
            return True
        if y1 == y2:
            continue
        intersects = ((y1 > y) != (y2 > y))
        if intersects:
            try:
                x_at_y = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            except ZeroDivisionError:  # pragma: no cover - defensive
                x_at_y = x1
            if math.isclose(x_at_y, x, rel_tol=0.0, abs_tol=1e-9):
                return True
            if x_at_y > x:
                inside = not inside
    return inside


def is_in_vienna(lat: object, lon: object) -> bool:
    """Return ``True`` if the coordinate lies within the Vienna city boundary."""

    polygons = _vienna_polygons()
    if not polygons:
        return False
    try:
        latitude = float(lat)
        longitude = float(lon)
    except (TypeError, ValueError):
        return False
    if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
        return False
    for outer, holes in polygons:
        if _point_in_ring(latitude, longitude, outer):
            if any(_point_in_ring(latitude, longitude, hole) for hole in holes):
                continue
            return True
    return False


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
def _station_lookup() -> Dict[str, StationInfo]:
    """Return a mapping from normalized aliases to :class:`StationInfo` records."""

    try:
        with _STATIONS_PATH.open("r", encoding="utf-8") as handle:
            entries = json.load(handle)
    except (OSError, json.JSONDecodeError):  # pragma: no cover - defensive
        return {}

    mapping: Dict[str, StationInfo] = {}
    if not isinstance(entries, list):
        return mapping

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        if not name:
            continue
        code_raw = entry.get("bst_code")
        code = str(code_raw).strip() if code_raw is not None else ""
        wl_diva_raw = entry.get("wl_diva")
        wl_diva = str(wl_diva_raw).strip() if wl_diva_raw is not None else ""
        extra_aliases: set[str] = set()
        if wl_diva:
            extra_aliases.add(wl_diva)
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
        record = StationInfo(
            name=name,
            in_vienna=bool(entry.get("in_vienna")),
            pendler=bool(entry.get("pendler")),
            wl_diva=wl_diva or None,
            wl_stops=tuple(stop_records),
        )
        for alias in _iter_aliases(name, code or None, extra_aliases):
            key = _normalize_token(alias)
            if not key:
                continue
            if key not in mapping:
                mapping[key] = record
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


def canonical_name(name: str) -> str | None:
    """Return the canonical ÖBB station name for *name* or ``None`` if unknown."""

    info = station_info(name)
    return info.name if info else None


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


def is_station_in_vienna(name: str) -> bool:
    """Return ``True`` if *name* refers to a station located in Vienna."""

    info = station_info(name)
    if info:
        return bool(info.in_vienna)
    if isinstance(name, str):
        token = _normalize_token(name)
        if token == "wien" or token.startswith("wien "):
            return True
    return False


def is_pendler(name: str) -> bool:
    """Return ``True`` if *name* is part of the configured commuter belt."""

    info = station_info(name)
    return bool(info and info.pendler)
