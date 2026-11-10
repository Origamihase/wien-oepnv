"""Helpers for working with the ÖBB station directory.

Public facade (700-line rule, 2026-11-02): the record types, paths and the
capped JSON reader live in ``stations_data``, name tokens and coercion in
``stations_normalize``, the polygon test in ``stations_geometry`` and the
name lookup in ``stations_lookup``. This module keeps the Vienna polygon,
rail proximity, open-data coordinates and the Vienna checks, and re-exports
the moved names.
"""

from __future__ import annotations

import csv
import io
import logging
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, NamedTuple
from collections.abc import Iterable

from .files import read_capped_text
from .geo import calculate_distance_meters
from .stations_data import (
    MAX_GEONETZ_STOPS_FILE_BYTES,
    MAX_STATIONS_FILE_BYTES,
    MAX_VIENNA_POLYGON_FILE_BYTES,
    MAX_WL_GTFS_STOPS_FILE_BYTES,
    MAX_WL_OGD_STOPS_FILE_BYTES,
    MAX_WL_STOP_DISTANCE_M,
    WL_NETWORK_CENTRE,
    StationInfo,
    _GEONETZ_STOPS_PATH,
    _VIENNA_POLYGON_PATH,
    _WL_GTFS_STOP_ID_RE,
    _WL_GTFS_STOPS_PATH,
    _WL_OGD_STOPS_PATH,
    _read_capped_json,
    _station_entries,
)
from .stations_geometry import Coordinate, Polygon, Ring, _point_in_polygon
from .stations_lookup import (
    _station_lookup,
    canonical_name,
    display_name,
    main_station_names,
    railway_station_names,
    railway_station_of,
    station_by_oebb_id,
    station_info,
    station_lines,
    station_named_in_text,
)
from .stations_normalize import _coerce_float, _coerce_lat, _coerce_lon, _normalize_token
from .term_search import WholeTermSearch

__all__ = [
    "canonical_name",
    "display_name",
    "is_in_vienna",
    "is_pendler",
    "nearest_rail_station",
    "railway_station_names",
    "railway_station_of",
    "open_data_coordinate",
    "station_by_oebb_id",
    "station_info",
    "station_lines",
    "station_named_in_text",
    "station_wl_lines",
    "text_has_vienna_connection",
    # Re-exported from the parts (importers, tests and wl_stations/base.py
    # read them here).
    "Coordinate",
    "MAX_GEONETZ_STOPS_FILE_BYTES",
    "MAX_STATIONS_FILE_BYTES",
    "MAX_VIENNA_POLYGON_FILE_BYTES",
    "MAX_WL_GTFS_STOPS_FILE_BYTES",
    "MAX_WL_OGD_STOPS_FILE_BYTES",
    "MAX_WL_STOP_DISTANCE_M",
    "Polygon",
    "Ring",
    "StationInfo",
    "WL_NETWORK_CENTRE",
    "_GEONETZ_STOPS_PATH",
    "_VIENNA_POLYGON_PATH",
    "_WL_GTFS_STOP_ID_RE",
    "_WL_GTFS_STOPS_PATH",
    "_WL_OGD_STOPS_PATH",
    "_coerce_float",
    "_coerce_lat",
    "_coerce_lon",
    "_normalize_token",
    "_point_in_polygon",
    "_read_capped_json",
    "_station_entries",
    "_station_lookup",
    "main_station_names",
]


logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _vienna_polygons() -> tuple[Polygon, ...]:
    """Return a tuple of polygon rings representing Vienna's city limits."""

    data = _read_capped_json(
        _VIENNA_POLYGON_PATH,
        MAX_VIENNA_POLYGON_FILE_BYTES,
        label="Vienna polygon",
    )
    if data is None:
        return ()

    polygons: list[Polygon] = []

    def add_polygon(coords: Iterable[Iterable[Iterable[float]]]) -> None:
        parsed_rings: list[Ring] = []
        for raw_ring in coords:
            ring_points: list[Coordinate] = []
            for pair in raw_ring:
                if not isinstance(pair, list | tuple) or len(pair) < 2:
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


@lru_cache(maxsize=1)
def _rail_station_coordinates() -> tuple[tuple[str, float, float], ...]:
    """Return ``(name, lat, lon)`` for every rail Betriebsstelle.

    A ``bst_id`` marks an ÖBB / S-Bahn operating point — i.e. a
    *Bahnhof* (52 of them inside Vienna) or a *Pendlerbahnhof* (109
    commuter stations). Pure Wiener-Linien tram/bus stops (``wl_diva``
    only, no ``bst_id``) are deliberately excluded: matching against the
    ~1800 stops would turn proximity into "near any stop", which in a
    dense city means "everywhere". Entries without a valid coordinate
    pair are skipped.
    """

    rail: list[tuple[str, float, float]] = []
    for entry in _station_entries():
        if not entry.get("bst_id"):
            continue
        lat = _coerce_lat(entry.get("latitude") or entry.get("lat"))
        lon = _coerce_lon(entry.get("longitude") or entry.get("lon"))
        if lat is None or lon is None:
            continue
        name = entry.get("name")
        if isinstance(name, str) and name.strip():
            rail.append((name.strip(), lat, lon))
    return tuple(rail)


def nearest_rail_station(
    lat: object, lon: object, radius_m: float
) -> tuple[str, float] | None:
    """Return ``(name, distance_m)`` of the closest rail Bahnhof within
    ``radius_m`` of the coordinate, or ``None`` if none qualifies.

    Inputs are coerced and range-checked via :func:`_coerce_lat` /
    :func:`_coerce_lon`, so ``None`` / ``NaN`` / out-of-range coordinates
    yield ``None`` (fail closed). The scan is ``O(n)`` over the cached
    rail set (~160 entries) with no regex/backtracking surface.
    """

    qlat = _coerce_lat(lat)
    qlon = _coerce_lon(lon)
    if qlat is None or qlon is None:
        return None
    if not radius_m > 0:
        return None
    best: tuple[str, float] | None = None
    for name, slat, slon in _rail_station_coordinates():
        distance = calculate_distance_meters(qlat, qlon, slat, slon)
        if distance <= radius_m and (best is None or distance < best[1]):
            best = (name, distance)
    return best


class OpenCoordinate(NamedTuple):
    """A station coordinate taken from an open dataset, with its origin."""

    latitude: float
    longitude: float
    # ``"oebb_geonetz"`` (ÖBB-Infrastruktur GeoNetz, CC BY 3.0 AT),
    # ``"wl_ogd"`` (Wiener Linien OGD Haltestellen, CC BY 4.0) or
    # ``"wl_gtfs"`` (Wiener Linien GTFS stops, CC BY 4.0, only where the
    # OGD coordinate is implausible).
    dataset: str


@lru_cache(maxsize=1)
def _entries_by_name() -> dict[str, dict[str, Any]]:
    """Directory entries keyed on their canonical ``name``."""

    result: dict[str, dict[str, Any]] = {}
    for entry in _station_entries():
        name = entry.get("name")
        if isinstance(name, str) and name and name not in result:
            result[name] = entry
    return result


@lru_cache(maxsize=1)
def _geonetz_coordinates() -> dict[str, tuple[float, float]]:
    """GeoNetz stop coordinates keyed ``eva:<EVA-Nr>`` and ``ifopt:<IFOPT>``."""

    payload = _read_capped_json(
        _GEONETZ_STOPS_PATH, MAX_GEONETZ_STOPS_FILE_BYTES, label="GeoNetz stops"
    )
    stops = payload.get("stops") if isinstance(payload, dict) else None
    if not isinstance(stops, list):
        return {}
    result: dict[str, tuple[float, float]] = {}
    for stop in stops:
        if not isinstance(stop, dict):
            continue
        lat = _coerce_lat(stop.get("lat"))
        lon = _coerce_lon(stop.get("lon"))
        if lat is None or lon is None:
            continue
        for prefix, field in (("eva", "eva_nr"), ("ifopt", "ifopt_id")):
            value = stop.get(field)
            if isinstance(value, str) and value.strip():
                result.setdefault(f"{prefix}:{value.strip()}", (lat, lon))
    return result


@lru_cache(maxsize=1)
def _wl_ogd_coordinates() -> dict[str, tuple[float, float]]:
    """Wiener Linien OGD stop coordinates keyed on the DIVA number."""

    text = read_capped_text(
        _WL_OGD_STOPS_PATH,
        MAX_WL_OGD_STOPS_FILE_BYTES,
        encoding="utf-8-sig",
        label="WL OGD Haltestellen",
        logger=logger,
    )
    if text is None:
        return {}
    result: dict[str, tuple[float, float]] = {}
    for row in csv.DictReader(io.StringIO(text), delimiter=";"):
        diva = (row.get("DIVA") or "").strip()
        lat = _coerce_lat(row.get("Latitude"))
        lon = _coerce_lon(row.get("Longitude"))
        if diva and lat is not None and lon is not None:
            result.setdefault(diva, (lat, lon))
    return result


def is_plausible_wl_coordinate(latitude: float, longitude: float) -> bool:
    """Whether a coordinate can belong to a Wiener-Linien stop.

    ``True`` while the point lies within :data:`MAX_WL_STOP_DISTANCE_M` of
    the network centre, ``False`` for a well-formed coordinate that cannot
    be a Wiener-Linien stop (the Waldviertel DIVAs above).
    """

    distance = calculate_distance_meters(
        latitude, longitude, WL_NETWORK_CENTRE[0], WL_NETWORK_CENTRE[1]
    )
    return distance <= MAX_WL_STOP_DISTANCE_M


def load_wl_gtfs_coordinates(path: Path | None = None) -> dict[str, tuple[float, float]]:
    """Mean stop coordinate per DIVA from the Wiener Linien GTFS ``stops.txt``.

    Only plausible points count (:func:`is_plausible_wl_coordinate`). A
    missing or unreadable file yields ``{}``: the GTFS stops are a fallback
    for broken OGD values, never required.
    """

    text = read_capped_text(
        path or _WL_GTFS_STOPS_PATH,
        MAX_WL_GTFS_STOPS_FILE_BYTES,
        encoding="utf-8-sig",
        label="WL GTFS stops",
        logger=logger,
    )
    if text is None:
        return {}
    points: dict[str, list[tuple[float, float]]] = {}
    for row in csv.DictReader(io.StringIO(text)):
        match = _WL_GTFS_STOP_ID_RE.match((row.get("stop_id") or "").strip())
        lat = _coerce_lat(row.get("stop_lat"))
        lon = _coerce_lon(row.get("stop_lon"))
        if match is None or lat is None or lon is None:
            continue
        if not is_plausible_wl_coordinate(lat, lon):
            continue
        diva = str(60200000 + int(match.group(1)))
        points.setdefault(diva, []).append((lat, lon))
    return {
        diva: (
            round(sum(p[0] for p in pts) / len(pts), 6),
            round(sum(p[1] for p in pts) / len(pts), 6),
        )
        for diva, pts in points.items()
    }


@lru_cache(maxsize=1)
def _wl_gtfs_coordinates() -> dict[str, tuple[float, float]]:
    return load_wl_gtfs_coordinates()


def _geonetz_coordinate(entry: dict[str, Any]) -> OpenCoordinate | None:
    table = _geonetz_coordinates()
    for prefix, field in (("eva", "eva_nr"), ("ifopt", "ifopt_id")):
        value = entry.get(field)
        if isinstance(value, str) and value.strip():
            hit = table.get(f"{prefix}:{value.strip()}")
            if hit:
                return OpenCoordinate(hit[0], hit[1], "oebb_geonetz")
    return None


def _wl_ogd_coordinate(entry: dict[str, Any]) -> OpenCoordinate | None:
    diva = entry.get("wl_diva")
    if diva is None:
        return None
    key = str(diva).strip()
    hit = _wl_ogd_coordinates().get(key)
    if hit and is_plausible_wl_coordinate(hit[0], hit[1]):
        return OpenCoordinate(hit[0], hit[1], "wl_ogd")
    # The OGD list misses the stop or places it outside the WL network: the
    # same stop in the Wiener Linien GTFS timetable, else no coordinate.
    fallback = _wl_gtfs_coordinates().get(key)
    return OpenCoordinate(fallback[0], fallback[1], "wl_gtfs") if fallback else None


def open_data_coordinate(name: str, *, prefer: str = "oebb") -> OpenCoordinate | None:
    """Coordinate of the station named *name* from an open dataset only.

    *name* is a canonical directory name (``StationInfo.name``). The
    coordinate comes from ÖBB-Infrastruktur's GeoNetz (joined on EVA or
    IFOPT number) or from the Wiener Linien OGD stop list (joined on the
    DIVA number), both CC BY 4.0. ``prefer="wl"`` tries the Wiener Linien
    list first (a WL stop notice), the default tries GeoNetz first (a rail
    notice).

    The directory's own ``latitude``/``longitude`` are deliberately not
    used: they are merged from ÖBB HAFAS as well, and the GeoRSS elements in the public feed must only carry
    coordinates whose origin and terms are known (operator decision
    2026-10-16). A station found in neither dataset yields ``None`` — no
    fallback.
    """

    entry = _entries_by_name().get(name)
    if entry is None:
        return None
    order = (_wl_ogd_coordinate, _geonetz_coordinate) if prefer == "wl" else (
        _geonetz_coordinate,
        _wl_ogd_coordinate,
    )
    for resolve in order:
        hit = resolve(entry)
        if hit is not None:
            return hit
    return None


def station_wl_lines(name: str) -> frozenset[str]:
    """Wiener Linien lines the directory lists for the station *name*."""

    entry = _entries_by_name().get(name)
    lines = entry.get("wl_lines") if entry else None
    if not isinstance(lines, list):
        return frozenset()
    return frozenset(str(line).strip() for line in lines if str(line).strip())


def is_in_vienna(lat: object, lon: object | None = None) -> bool:
    """Return ``True`` if the supplied coordinates or station name lie in Vienna."""

    if lon is None:
        if isinstance(lat, str):
            info = station_info(lat)
            if info:
                return bool(info.in_vienna)
            # _normalize_token casefolds and strips noise — apply the same
            # normalisation to the env-derived city token so a deployer
            # setting WIEN_TOKEN="Wien" (or any cased variant) still
            # matches against the casefolded incoming station name.
            city_token = _normalize_token(os.getenv("WIEN_TOKEN", "wien"))
            token = _normalize_token(lat)
            if city_token and (token == city_token or token.startswith(city_token + " ")):
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
    """Return ``True`` if *name* is part of the configured commuter belt.

    ``pendler`` is a *manually curated* marking, unlike ``in_vienna`` which
    :func:`is_in_vienna` derives automatically from the station's coordinates
    against the official LANDESGRENZE polygon. An operator nominates a
    commuter station by bst_id in ``data/pendler_bst_ids.json`` or by name in
    ``data/pendler_candidates.json``; ``scripts/update_station_directory.py``
    (``_annotate_station_flags``) then sets the flag, with ``in_vienna``
    winning when both would apply — the two are mutually exclusive.

    Args:
        name: A station name or alias; resolved via :func:`station_info`.

    Returns:
        ``True`` when the resolved directory entry carries ``pendler: true``,
        ``False`` when it does not or the name does not resolve at all.
    """

    info = station_info(name)
    return bool(info and info.pendler)


@lru_cache(maxsize=1)
def _non_vienna_stations_regex() -> re.Pattern[str] | None:
    """Kompiliert einen Regex-Ausdruck mit allen bekannten Nicht-Wien/Nicht-Pendler Stationen."""
    non_vienna: set[str] = set()
    for entry in _station_entries():
        if entry.get("in_vienna") or entry.get("pendler"):
            continue

        name = str(entry.get("name", "")).strip()
        if name and not name.isdigit() and len(name) >= 4:
            non_vienna.add(name)

        aliases = entry.get("aliases")
        for alias in aliases if isinstance(aliases, list) else []:
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
def _vienna_stations_regex() -> WholeTermSearch:
    """Die Suche nach allen bekannten Wiener Stationen als ganzer Begriff.

    Bis 2026-11-10 ein einziges Muster mit 159.754 Alternativen: 7 s zum
    Kompilieren und im Mittel rund 1 s je ÖBB-Text. :class:`WholeTermSearch`
    gibt dieselbe Antwort über einen Index (``src/utils/term_search.py``).
    """
    vienna: set[str] = set()
    for entry in _station_entries():
        if entry.get("in_vienna"):
            name = str(entry.get("name", "")).strip().lower()
            if name:
                vienna.add(name)
            aliases = entry.get("aliases")
            for alias in aliases if isinstance(aliases, list) else []:
                if alias:
                    vienna.add(str(alias).strip().lower())

    # Filtere ungenaue Alias-Namen und reine Zahlen (z.B. IDs) heraus. Die
    # Mindestlänge 4 (wie in :func:`_non_vienna_stations_regex`) entfernt die
    # opaken 3-Zeichen ÖBB-Betriebsstellencodes (HAK/STK/REN/HET/SUE/…), die
    # sonst als ganzes Wort auf Alltagstoken wie die Handelsakademie-Abkürzung
    # "HAK" matchen und ein falsches Wien-Signal liefern — echte Meldungen
    # referenzieren diese Stationen über den Vollnamen (Bug b11).
    vienna = {n for n in vienna if len(n) >= 4 and not n.isdigit()}
    vienna -= {"hbf", "bf", "bahnhof", "hauptbahnhof", "station"}

    return WholeTermSearch(vienna)


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

    # 2. Prüfe auf das eigenständige Wort "Wien" / "Vienna".
    # Achtung: ein nacktes "U-Bahn" allein ist NICHT spezifisch genug —
    # "Berliner U-Bahn", "Münchner U-Bahn-Linie U4" oder "Hamburger U-Bahn"
    # würden sonst als Wien-Bezug gewertet und als ÖBB-Item ins Feed
    # rutschen, obwohl die Meldung eine fremde Stadt betrifft. Daher: für
    # U-Bahn-Mention zusätzlich Wien-Wort fordern.
    if re.search(r"\b(wien|vienna)\b", cleaned, re.IGNORECASE):
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
    return _vienna_stations_regex().search(cleaned)
