"""Helpers for working with the ÖBB station directory."""

from __future__ import annotations

import json
import logging
import math
import re
import unicodedata
from enum import IntEnum
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, NamedTuple

__all__ = [
    "canonical_name",
    "is_in_vienna",
    "is_pendler",
    "station_by_oebb_id",
    "station_info",
    "text_has_vienna_connection",
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

    val: float
    if isinstance(value, (int, float)):
        val = float(value)
    else:
        text = str(value).strip()
        if not text:
            return None
        text = text.replace(",", ".")
        try:
            val = float(text)
        except ValueError:
            return None

    if not math.isfinite(val):
        return None
    return val


def _coerce_lat(value: object | None) -> float | None:
    """Coerce value to a valid latitude."""
    val = _coerce_float(value)
    if val is not None and (46.0 <= val <= 49.5):
        return val
    return None


def _coerce_lon(value: object | None) -> float | None:
    """Coerce value to a valid longitude."""
    val = _coerce_float(value)
    if val is not None and (9.0 <= val <= 17.5):
        return val
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
                lon = _coerce_lon(pair[0])
                lat = _coerce_lat(pair[1])
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


class _MatchStrength(IntEnum):
    """Strength of a match between an alias token and a station record.

    Higher values win in tie-breaking. ``IDENTITY``-class tokens come from
    fields that uniquely identify a station (``bst_code``, ``bst_id``,
    ``vor_id``, ``wl_diva``, ``stop_id``). ``TEXT``-class tokens come from
    the canonical name, its textual variations, and the freeform
    ``aliases`` list. An IDENTITY match by one station outranks a TEXT
    match by another station for the same token, which is what prevents a
    foreign station's ``aliases`` entry from shadowing the rightful
    ID-bearing station (the root cause of the '900100' bug, see #1082).
    """

    TEXT = 1
    IDENTITY = 2


def _iter_aliases_with_strength(
    name: str,
    code: str | None,
    identity_extras: Iterable[str] | None = None,
    text_extras: Iterable[str] | None = None,
) -> list[tuple[str, _MatchStrength]]:
    """Yield ``(alias, strength)`` pairs for a station entry.

    The canonical *name* and its variations (bracket-stripped, hyphen and
    slash flattened, St./Sankt forms) are emitted as
    :attr:`_MatchStrength.TEXT`. The *code* (a station's ``bst_code``) and
    any *identity_extras* (``vor_id``, ``wl_diva``, stop IDs) are emitted
    as :attr:`_MatchStrength.IDENTITY`. Entries in *text_extras*
    (``aliases`` array, stop names) are emitted as TEXT.

    Order is preserved from the legacy :func:`_iter_aliases`: first the
    name and its variations, then identity_extras, then text_extras. The
    first occurrence of a candidate string wins, so identity-class wins
    over text-class on duplicates within a single station entry.
    """

    seen: set[str] = set()
    variants: list[tuple[str, _MatchStrength]] = []

    def add(raw: str, strength: _MatchStrength) -> None:
        candidate = re.sub(r"\s{2,}", " ", raw.strip())
        if candidate and candidate not in seen:
            seen.add(candidate)
            variants.append((candidate, strength))

    add(name, _MatchStrength.TEXT)
    if code:
        add(code, _MatchStrength.IDENTITY)

    no_paren = re.sub(r"\s*\([^)]*\)\s*", " ", name)
    add(no_paren, _MatchStrength.TEXT)
    add(no_paren.replace("-", " "), _MatchStrength.TEXT)
    add(no_paren.replace("/", " "), _MatchStrength.TEXT)
    add(name.replace("-", " "), _MatchStrength.TEXT)
    add(name.replace("/", " "), _MatchStrength.TEXT)

    if re.search(r"\bSt\.?\b", name):
        add(re.sub(r"\bSt\.?\b", "St", name), _MatchStrength.TEXT)
        add(re.sub(r"\bSt\.?\b", "St ", name), _MatchStrength.TEXT)
        add(re.sub(r"\bSt\.?\b", "Sankt ", name), _MatchStrength.TEXT)
    if re.search(r"\bSankt\b", name):
        add(re.sub(r"\bSankt\b", "St.", name), _MatchStrength.TEXT)
        add(re.sub(r"\bSankt\b", "St ", name), _MatchStrength.TEXT)
        add(re.sub(r"\bSankt\b", "St", name), _MatchStrength.TEXT)

    if identity_extras:
        for extra in identity_extras:
            add(str(extra), _MatchStrength.IDENTITY)

    if text_extras:
        for extra in text_extras:
            add(str(extra), _MatchStrength.TEXT)

    return variants


def _iter_aliases(
    name: str, code: str | None, extras: Iterable[str] | None = None
) -> Iterable[str]:
    """Yield alias strings for a station entry (canonical name first).

    Backward-compatible wrapper around :func:`_iter_aliases_with_strength`
    that drops the strength annotation. Treats *extras* as TEXT-class.
    Existing external callers (diagnostic scripts, validators) keep
    working unchanged.
    """

    return [
        alias
        for alias, _ in _iter_aliases_with_strength(
            name, code, identity_extras=None, text_extras=extras
        )
    ]


@lru_cache(maxsize=1)
def _station_entries() -> tuple[dict[str, Any], ...]:
    """Return the raw station entries from :mod:`data/stations.json`."""

    try:
        with _STATIONS_PATH.open("r", encoding="utf-8") as handle:
            entries = json.load(handle)
    except (OSError, json.JSONDecodeError):  # pragma: no cover - defensive
        return ()

    if isinstance(entries, dict):
        entries = entries.get("stations", [])

    if not isinstance(entries, list):
        return ()

    result: list[dict[str, Any]] = []
    for entry in entries:
        if isinstance(entry, dict):
            result.append(entry)
    return tuple(result)


@lru_cache(maxsize=1)
def _station_lookup() -> dict[str, StationInfo]:
    """Return a mapping from normalized aliases to :class:`StationInfo` records.

    The internal mapping carries match-strength alongside each record so
    that a later entry whose alias matches via a stronger class
    (:attr:`_MatchStrength.IDENTITY`) can evict an earlier weaker match
    (:attr:`_MatchStrength.TEXT`). The strength annotation is dropped from
    the returned mapping; only :class:`StationInfo` is exposed.
    """

    mapping: dict[str, tuple[StationInfo, _MatchStrength]] = {}

    for entry in _station_entries():
        name = str(entry.get("name", "")).strip()
        if not name:
            continue
        code_raw = entry.get("bst_code")
        code = str(code_raw).strip() if code_raw is not None else ""
        wl_diva_raw = entry.get("wl_diva")
        wl_diva = str(wl_diva_raw).strip() if wl_diva_raw is not None else ""
        vor_id_raw = entry.get("vor_id") or entry.get("id")
        vor_id = str(vor_id_raw).strip() if vor_id_raw is not None else ""

        # Identity-class aliases: authoritative IDs that uniquely identify
        # this station. A match against any of these outranks any text alias.
        identity_aliases: set[str] = set()
        if wl_diva:
            identity_aliases.add(wl_diva)
        if vor_id:
            identity_aliases.add(vor_id)

        # Text-class aliases: the explicit ``aliases`` list and stop names.
        # Conflicts between two text matches fall through to the historical
        # source-based tie-break below.
        text_aliases: set[str] = set()
        aliases_field = entry.get("aliases")
        if isinstance(aliases_field, list):
            for alias in aliases_field:
                if alias is None:
                    continue
                alias_text = str(alias).strip()
                if alias_text:
                    text_aliases.add(alias_text)

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
                latitude = _coerce_lat(stop.get("latitude"))
                longitude = _coerce_lon(stop.get("longitude"))
                if stop_id:
                    # Stop IDs are authoritative and rank as IDENTITY.
                    identity_aliases.add(stop_id)
                if stop_name:
                    text_aliases.add(stop_name)
                stop_records.append(
                    WLStop(
                        stop_id=stop_id,
                        name=stop_name or None,
                        latitude=latitude,
                        longitude=longitude,
                    )
                )
        station_latitude = _coerce_lat(entry.get("latitude") or entry.get("lat"))
        station_longitude = _coerce_lon(entry.get("longitude") or entry.get("lon"))
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
        for alias_text, strength in _iter_aliases_with_strength(
            name,
            code or None,
            identity_extras=identity_aliases,
            text_extras=text_aliases,
        ):
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
                mapping[key] = (alias_record, strength)
                continue
            existing_record, existing_strength = existing

            if existing_record == alias_record or existing_record.name == alias_record.name:
                continue

            # Match-strength precedence: an IDENTITY-class match by one
            # station outranks a TEXT-class match by another station for
            # the same token. This prevents a foreign station's aliases
            # entry from shadowing the rightful ID-bearing station (the
            # root cause of the '900100' bug, see #1082).
            if strength > existing_strength:
                mapping[key] = (alias_record, strength)
                continue
            if strength < existing_strength:
                continue

            # Equal strength: fall back to the historical source-based and
            # name-token-based tie-break. The branches below are wordwise
            # identical to the pre-refactor logic; only the mapping value
            # type carries the strength annotation now.
            existing_source = existing_record.source or ""
            record_source = alias_record.source or ""
            if existing_source == "combined":
                existing_source = "wl"
            if record_source == "combined":
                record_source = "wl"

            alias_token = _normalize_token(alias_text)
            record_token = _normalize_token(alias_record.name)
            existing_token = _normalize_token(existing_record.name)

            if alias_token and alias_token == record_token and alias_token != existing_token:
                mapping[key] = (alias_record, strength)
                continue
            if alias_token and alias_token == existing_token and alias_token != record_token:
                continue

            if existing_record.vor_id and alias_record.vor_id and existing_record.vor_id == alias_record.vor_id:
                if alias_is_numeric or alias_mentions_vor:
                    if record_source == "vor" and existing_source != "vor":
                        mapping[key] = (alias_record, strength)
                else:
                    if record_source == "wl" and existing_source != "wl":
                        mapping[key] = (alias_record, strength)
                continue

            if existing_source == "vor" and record_source != "vor":
                mapping[key] = (alias_record, strength)
                continue
            if record_source == "vor" and existing_source != "vor":
                continue
            logger.warning(
                "Duplicate station alias %r normalized to %r for %s conflicts with %s",
                alias_text,
                key,
                alias_record.name,
                existing_record.name,
            )

    # Drop the strength annotation from the public mapping.
    return {key: record for key, (record, _) in mapping.items()}


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
    target_id = str(bst_id).strip()

    for entry in _station_entries():
        current_id = entry.get("bst_id")
        if current_id is not None and str(current_id).strip() == target_id:
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

    if lon is None:
        if isinstance(lat, str):
            info = station_info(lat)
            if info:
                return bool(info.in_vienna)
            import os
            city_token = os.getenv("WIEN_TOKEN", "wien")
            token = _normalize_token(lat)
            if token == city_token or token.startswith(city_token + " "):
                return True
        return False

    latitude = _coerce_lat(lat)
    longitude = _coerce_lon(lon)
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
        vor_id_raw = entry.get("vor_id") or entry.get("id")
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


@lru_cache(maxsize=1)
def _non_vienna_stations_regex() -> 're.Pattern[str] | None':
    """Kompiliert einen Regex-Ausdruck mit allen bekannten Nicht-Wien/Nicht-Pendler Stationen."""
    non_vienna: set[str] = set()
    for entry in _station_entries():
        if entry.get("in_vienna") or entry.get("pendler"):
            continue

        name = str(entry.get("name", "")).strip()
        if name and not name.isdigit() and len(name) >= 4:
            non_vienna.add(name)

        for alias in entry.get("aliases", []):
            if alias:
                alias_str = str(alias).strip()
                if alias_str and not alias_str.isdigit() and len(alias_str) >= 4:
                    non_vienna.add(alias_str)

    if not non_vienna:
        return None

    sorted_terms = sorted(non_vienna, key=len, reverse=True)

    # Ergänze optionale generische Suffixe für Bahnhöfe
    suffixes = (
        r"(?:\s+(?:Hbf|Hauptbahnhof|Westbahnhof|Ostbahnhof|"
        r"Südbahnhof|Nordbahnhof|Bahnhof|Bf|hl\.?\s*st\.?|"
        r"hlavní\s+nádraží|Keleti|Nyugati|Déli)(?!\w))?"
    )
    pattern = r"(?<!\w)(?:" + "|".join(re.escape(t) for t in sorted_terms) + r")(?!\w)" + suffixes
    return re.compile(pattern, re.IGNORECASE)


def _mask_non_vienna_stations(text: str) -> str:
    """Maskiert bekannte Nicht-Wien/Nicht-Pendler Stationen im Text."""
    regex = _non_vienna_stations_regex()
    if regex:
        return regex.sub(" ", text)
    return text


@lru_cache(maxsize=1)
def _vienna_stations_regex() -> 're.Pattern[str]':
    """Kompiliert einen Regex-Ausdruck mit allen bekannten Wiener Stationen."""
    vienna: set[str] = set()
    for entry in _station_entries():
        if entry.get("in_vienna"):
            name = str(entry.get("name", "")).strip().lower()
            if name:
                vienna.add(name)
            for alias in entry.get("aliases", []):
                if alias:
                    vienna.add(str(alias).strip().lower())

    # Filtere ungenaue Alias-Namen und reine Zahlen (z.B. IDs) heraus
    vienna = {n for n in vienna if len(n) >= 3 and not n.isdigit()}
    vienna -= {"hbf", "bf", "bahnhof", "hauptbahnhof", "station"}

    if not vienna:
        return re.compile(r"(?!x)x")

    sorted_terms = sorted(vienna, key=len, reverse=True)
    pattern = r"(?<!\w)(?:" + "|".join(re.escape(t) for t in sorted_terms) + r")(?!\w)"
    return re.compile(pattern, re.IGNORECASE)


def text_has_vienna_connection(text: str) -> bool:
    if not text:
        return False

    # 0. Dynamically mask specific non-Vienna/non-commuter locations
    text = _mask_non_vienna_stations(text)

    # 0a. Maskiere spezifische Nicht-Wien-Orte ohne generisches Suffix
    # Dies verhindert Verwechslungen wie Hadersdorf am Kamp (NÖ) vs. Wien Hadersdorf.
    text_for_matching = re.sub(r"Hadersdorf am Kamp", " ", text, flags=re.IGNORECASE)

    # 0b. Maskiere Nicht-Wiener Stationen, die Wörter enthalten, die mit Wiener Stationen matchen
    text_for_matching = re.sub(
        r"(Villach|Innsbruck|Linz|Graz|Salzburg|Klagenfurt)\s+(Westbahnhof|Ostbahnhof|Hbf|Hauptbahnhof|Süd|Nord)",
        " ",
        text_for_matching,
        flags=re.IGNORECASE,
    )

    # 1. Pendler-Spezialfälle maskieren (verhindert False-Positive beim Wort "Wien")
    cleaned = re.sub(r"Flughafen Wien|Airport Vienna|Vienna Airport", " ", text_for_matching, flags=re.IGNORECASE)

    # Neu: Flughafen Wien explizit als True werten (laut Anforderung)
    if re.search(r"\b(flughafen wien|airport vienna|vienna airport)\b", text_for_matching, re.IGNORECASE):
        return True

    # 2. Prüfe auf das eigenständige Wort "Wien" (z.B. als Richtungshinweis) oder U-Bahn
    if re.search(r"\b(wien|vienna|u-bahn)\b", cleaned, re.IGNORECASE):
        return True

    # 3. Kontextsensitive Erkennung für U1-U6:
    # Matcht nur, wenn U1-U6 in typischen Mustern auftritt (z.B. "Linie U6", "der U6", "U1:", "(U2)")
    # oder wenn typische Öffi-Wörter nahestehen, um False-Positives ("Zürich U4") zu vermeiden.
    u_bahn_pattern = (
        r"(?:\b(?:linie|der|die|auf|mit|von|zur)\s+u[1-6]\b|"
        r"\bu[1-6]\b\s*[:(]|"
        r"\(\s*u[1-6]\s*\)|"
        r"\bu[1-6]\b(?=\s*(?:steht|fährt|ersatz|halt|störung|gesperrt|unterbrochen)))"
    )
    if re.search(u_bahn_pattern, cleaned, re.IGNORECASE):
        return True

    # 4. Abgleich gegen Wiener Stationen und Aliase aus dem Verzeichnis
    rx = _vienna_stations_regex()
    if rx.search(cleaned):
        return True

    return False
