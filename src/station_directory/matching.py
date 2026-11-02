"""VOR matching, name harmonisation and Vienna/pendler flags of the station-directory updater.

Moved verbatim out of ``scripts/update_station_directory.py``, which
re-exports every name it or its tests use.
"""

from __future__ import annotations

import logging
import math
from copy import deepcopy
from collections.abc import Iterable, Mapping

from src.station_directory.model import (
    LocationInfo,
    Station,
    VORStop,
    _harmonize_station_name,
    _normalize_location_keys,
)
from src.utils.geo import use_cached_polygon_result
from src.utils.logging import sanitize_log_arg
from src.utils.stations import is_in_vienna as _is_point_in_vienna

logger = logging.getLogger("update_station_directory")


def _restore_existing_metadata(stations: Iterable[Station], existing_entries: dict[str, dict[str, object]]) -> None:
    for station in stations:
        try:
            lookup_id = str(int(float(station.bst_id)))
        except (ValueError, TypeError):
            lookup_id = str(station.bst_id)
        existing = existing_entries.get(lookup_id)
        if not existing:
            continue
        vor_id_raw = existing.get("vor_id")
        if isinstance(vor_id_raw, str):
            vor_id = vor_id_raw.strip()
            if vor_id:
                station.vor_id = vor_id
        for key, value in existing.items():
            if key in {"bst_id", "bst_code", "name", "in_vienna", "pendler", "vor_id"}:
                continue
            station.extras[key] = deepcopy(value)


def _looks_like_vienna(text: str | None) -> bool:
    if not text:
        return False
    normalized = text.strip().casefold()
    if not normalized.startswith("wien"):
        return False
    if len(normalized) == 4:
        return True
    return not normalized[4].isalpha()


def _vor_alias_candidates(stop: VORStop) -> set[str]:
    aliases: set[str] = {stop.name, stop.vor_id}
    if stop.short_name:
        aliases.add(stop.short_name)
    if stop.municipality:
        combined = f"{stop.municipality} {stop.name}".strip()
        aliases.add(combined)
    return {alias for alias in aliases if alias}


def _build_vor_index(stops: Iterable[VORStop]) -> dict[str, list[VORStop]]:
    index: dict[str, list[VORStop]] = {}
    for stop in stops:
        tokens: set[str] = set()
        for alias in _vor_alias_candidates(stop):
            tokens.update(_normalize_location_keys(alias))
        for token in tokens:
            if not token:
                continue
            index.setdefault(token, []).append(stop)
    return index


def _select_vor_stop(station: Station, candidates: list[VORStop]) -> VORStop | None:
    unique: dict[str, VORStop] = {}
    for stop in candidates:
        if stop.vor_id not in unique:
            unique[stop.vor_id] = stop
    stops = list(unique.values())
    if not stops:
        return None
    if len(stops) == 1:
        return stops[0]

    def _is_vienna_stop(stop: VORStop) -> bool:
        return _looks_like_vienna(stop.municipality) or _looks_like_vienna(stop.name)

    if station.in_vienna:
        vienna_stops = [stop for stop in stops if _is_vienna_stop(stop)]
        if len(vienna_stops) == 1:
            return vienna_stops[0]
        if vienna_stops:
            stops = vienna_stops
    else:
        outside = [stop for stop in stops if not _is_vienna_stop(stop)]
        if len(outside) == 1:
            return outside[0]
        if outside:
            stops = outside

    normalized_station = _harmonize_station_name(station.name).casefold()
    name_matches = [stop for stop in stops if _harmonize_station_name(stop.name).casefold() == normalized_station]
    if len(name_matches) == 1:
        return name_matches[0]
    return None


def _assign_vor_ids(
    stations: list[Station],
    vor_stops: list[VORStop],
    name_to_vor_id: Mapping[str, str] | None = None,
) -> None:
    name_map = name_to_vor_id or {}
    if not vor_stops and not name_map:
        return
    index = _build_vor_index(vor_stops) if vor_stops else {}
    # Track which vor_ids are already claimed by another station so we
    # never assign the same VOR id twice — that produces the
    # cross_station_id_issues collision the 2026-05 cron exposed
    # (Mistelbach + Mistelbach Stadt both ending up with 430420200).
    # Pre-load existing assignments from `vor_id` already on the
    # stations (bypasses idempotency on re-runs).
    used_vor_ids: set[str] = {station.vor_id for station in stations if station.vor_id}

    def _try_claim(station: Station, vor_id: str) -> bool:
        if vor_id in used_vor_ids:
            logger.warning(
                "Refusing to assign vor_id=%s to %s (bst_id=%s) — already "
                "claimed by another station; the fetcher resolved both "
                "names to the same VOR stop",
                vor_id,
                station.name,
                station.bst_id,
            )
            return False
        station.vor_id = vor_id
        used_vor_ids.add(vor_id)
        return True

    for station in stations:
        if station.vor_id:
            continue
        # Direct lookup via the fetcher-produced mapping.json wins —
        # avoids fuzzy-matching ambiguity when the resolved name differs
        # from the station name (e.g. Hohenau → Hohenau an der March Bahnhof).
        direct = name_map.get(station.name)
        if direct:
            _try_claim(station, direct)
            continue
        if not index:
            continue
        tokens = _normalize_location_keys(station.name)
        if not tokens:
            continue
        candidates: list[VORStop] = []
        for token in tokens:
            candidates.extend(index.get(token, []))
        if not candidates:
            continue
        selected = _select_vor_stop(station, candidates)
        if selected:
            _try_claim(station, selected.vor_id)
        else:
            logger.debug("Ambiguous VOR stop candidates for %s (%s)", station.name, station.bst_id)


def _harmonize_station_names(
    stations: list[Station],
    existing_entries: dict[str, dict[str, object]],
) -> None:
    if not existing_entries:
        for station in stations:
            station.name = _harmonize_station_name(station.name)
        return

    for station in stations:
        try:
            lookup_id = str(int(float(station.bst_id)))
        except (ValueError, TypeError):
            lookup_id = str(station.bst_id)
        existing = existing_entries.get(lookup_id)
        if existing:
            name_raw = existing.get("name")
            if isinstance(name_raw, str) and name_raw.strip():
                canonical = _harmonize_station_name(name_raw)
                if canonical and canonical != station.name:
                    logger.debug(
                        "Using existing name for %s: %s -> %s",
                        station.bst_id,
                        station.name,
                        canonical,
                    )
                station.name = canonical or station.name
                continue
        station.name = _harmonize_station_name(station.name)


def _is_vienna_station(name: str) -> bool:
    text = name.strip()
    if not text.startswith("Wien"):
        return False
    if len(text) == 4:
        return True
    return len(text) > 4 and text[4] in {" ", "-", "/", "("}


# Cache key: ``Station.extras["_in_vienna_basis"]`` — list of three
# elements ``[basis_lat, basis_lon, polygon_result]`` produced by the
# previous ``_resolve_in_vienna_with_cache`` call. Survives across runs
# via ``_restore_existing_metadata`` because it lives in ``extras``
# (not in the base-key skip list). Stored as a JSON-friendly list (not
# a tuple) so JSON load/save round-trips cleanly.
_IN_VIENNA_CACHE_KEY = "_in_vienna_basis"


def _resolve_in_vienna_with_cache(station: Station, info: LocationInfo) -> bool:
    """Compute ``in_vienna`` for ``station``, reusing the cached result
    when the supplied :class:`LocationInfo` coords haven't drifted past
    :data:`STATION_DRIFT_TOLERANCE_METERS` since the last polygon
    evaluation.

    Why: ``_is_point_in_vienna`` runs a ray-casting algorithm against
    the LANDESGRENZEOGD.json multipolygon (~thousands of vertices). At
    several hundred stations per run the aggregate cost is
    non-trivial, and the *result* is stable for any drift below 150 m
    (the city boundary tolerance is much wider than provider drift).
    Reusing the previous run's result when drift is below threshold
    eliminates the recomputation entirely.

    The cache is stored in ``station.extras[_IN_VIENNA_CACHE_KEY]`` as
    ``[basis_lat, basis_lon, polygon_result]`` — JSON-friendly so it
    survives stations.json round-trips. The drift comparison uses the
    canonical ``use_cached_polygon_result`` helper from
    ``src/utils/geo.py``; the basis key is read defensively against
    schema drift (length checks, type checks) and falls back to a
    full polygon evaluation on any malformed cache.
    """
    cache = station.extras.get(_IN_VIENNA_CACHE_KEY)
    cached_lat: float | None = None
    cached_lon: float | None = None
    cached_result: bool | None = None
    if isinstance(cache, list) and len(cache) == 3:
        raw_lat, raw_lon, raw_result = cache
        if isinstance(raw_lat, int | float):
            cached_lat = float(raw_lat)
        if isinstance(raw_lon, int | float):
            cached_lon = float(raw_lon)
        if isinstance(raw_result, bool):
            cached_result = raw_result

    cached = use_cached_polygon_result(
        cached_lat, cached_lon, cached_result, info.latitude, info.longitude
    )
    if cached is not None:
        return cached

    # Cache miss — run the real polygon check and refresh the cache.
    result = bool(_is_point_in_vienna(info.latitude, info.longitude))
    station.extras[_IN_VIENNA_CACHE_KEY] = [
        info.latitude, info.longitude, result
    ]
    return result


def _annotate_station_flags(
    stations: list[Station],
    pendler_ids: set[str],
    locations: Mapping[str, LocationInfo],
    pendler_name_candidates: set[str] | None = None,
    geonetz_lookup: Mapping[str, Mapping[str, object]] | None = None,
) -> None:
    """Set ``in_vienna`` and ``pendler`` mutually exclusively.

    A station whose ``bst_id`` has a GeoNetz point is checked against the
    city boundary with that point, not with the name-keyed *locations*:
    a name can belong to another place. ``Siebenhirten`` (bst_id 1371,
    near Mistelbach) matched the Wiener Linien stop of that name in
    Liesing and counted as Vienna until 2026-10-17. For the same reason
    the Wiener-Linien auto-promotion to ``pendler`` (rule 3 below) only
    applies when the name-keyed location is the one that was checked.

    A station is either inside the Vienna city limits *or* a commuter-belt
    station outside; never both. If a Vienna station's bst_id is mistakenly
    listed in ``data/pendler_bst_ids.json``, the ``in_vienna`` flag wins and
    the pendler flag stays ``False`` — a warning is logged so the entry can
    be removed from the whitelist.

    Pendler classification sources (any of them is sufficient):
      1. ``bst_id in pendler_ids`` — legacy bst_id whitelist (`data/pendler_bst_ids.json`).
      2. ``normalized name in pendler_name_candidates`` — name-based wishlist
         (`data/pendler_candidates.json`). Lets the user nominate stations
         without knowing the bst_id; the next ÖBB Excel pull resolves it.
      3. WL-sourced location outside Vienna — auto-promoted (legacy heuristic).
    """
    name_candidates = pendler_name_candidates or set()
    for station in stations:
        info: LocationInfo | None = None
        for key in _normalize_location_keys(station.name):
            info = locations.get(key)
            if info:
                break
        # Resolve in_vienna against the strongest available geo signal.
        # Priority order:
        #   (a) Fresh upstream LocationInfo from this run.
        #   (b) Coords carried forward in ``extras`` by
        #       ``_restore_existing_metadata`` from the prior
        #       stations.json — already validated and inertia-merged.
        #   (c) Name heuristic, last resort.
        # Without (b), a single failed WL/ÖBB/VOR fetch (info=None) for a
        # U-Bahn stop like "Stephansplatz" — whose name does NOT start
        # with "Wien" — would silently flip ``in_vienna`` to False even
        # though the persisted coords clearly fall inside the LANDESGRENZE
        # polygon. The synthetic LocationInfo carries ``sources={"extras"}``
        # so the WL-auto-promote check below remains a no-op for this path.
        polygon_info: LocationInfo | None = info
        geonetz_info = _geonetz_point(station, geonetz_lookup)
        if geonetz_info is not None:
            polygon_info = geonetz_info
        if polygon_info is None:
            extras_lat = station.extras.get("latitude")
            extras_lon = station.extras.get("longitude")
            if isinstance(extras_lat, int | float) and isinstance(
                extras_lon, int | float
            ):
                polygon_info = LocationInfo(
                    latitude=float(extras_lat),
                    longitude=float(extras_lon),
                    sources={"extras"},
                )
        if polygon_info is not None:
            # Coordinate Inertia (boundary-check layer): reuse the
            # previous polygon result when info coords haven't drifted
            # past STATION_DRIFT_TOLERANCE_METERS. See
            # ``_resolve_in_vienna_with_cache`` for the full rationale.
            station.in_vienna = _resolve_in_vienna_with_cache(
                station, polygon_info
            )
        else:
            station.in_vienna = _is_vienna_station(station.name)
        pendler_candidate = station.bst_id in pendler_ids
        if not pendler_candidate and name_candidates:
            for key in _normalize_location_keys(station.name):
                if key and key in name_candidates:
                    pendler_candidate = True
                    break
        if (
            info
            and polygon_info is info
            and not station.in_vienna
            and "wl" in info.sources
        ):
            pendler_candidate = True
        if station.in_vienna and pendler_candidate:
            logger.warning(
                "Station %s (bst_id=%s) is inside Vienna; ignoring pendler "
                "marker — remove the entry from data/pendler_bst_ids.json "
                "or data/pendler_candidates.json",
                station.name,
                station.bst_id,
            )
            pendler_candidate = False
        station.pendler = pendler_candidate


def _geonetz_point(
    station: Station, geonetz_lookup: Mapping[str, Mapping[str, object]] | None
) -> LocationInfo | None:
    """GeoNetz point of *station*'s ``bst_id`` as a :class:`LocationInfo`."""
    if not geonetz_lookup or not station.bst_id:
        return None
    try:
        lookup_id = str(int(float(station.bst_id)))
    except (TypeError, ValueError):
        lookup_id = str(station.bst_id)
    geo = geonetz_lookup.get(lookup_id)
    if geo is None:
        return None
    lat, lon = geo.get("lat"), geo.get("lon")
    if isinstance(lat, bool) or isinstance(lon, bool):
        return None
    if not (isinstance(lat, int | float) and isinstance(lon, int | float)):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return LocationInfo(latitude=float(lat), longitude=float(lon), sources={"oebb_geonetz"})


def _drop_duplicate_eva(
    entries: list[dict[str, object]], existing_ids: set[str]
) -> list[dict[str, object]]:
    """Keep one directory entry per EVA number (``eva_nr``).

    The ÖBB workbook can list one station under two ``bst_id`` values
    (``Handelskai`` 1586 next to ``Wien Handelskai`` 779, both EVA
    8101934), and a synthetic entry can stand in for a workbook station
    the boundary check used to drop (``Wien Kaiserebersdorf`` 900105 for
    bst_id 1059, EVA 8100374). Two entries for one station split its
    aliases, so the entry already in the directory wins: a manual entry
    or one whose ``bst_id`` the previous run had (*existing_ids*); else
    the first. Entries without an EVA number pass through.
    """

    def _known(entry: dict[str, object]) -> bool:
        bst_id = entry.get("bst_id")
        if bst_id is None:
            return True
        try:
            key = str(int(float(str(bst_id))))
        except (TypeError, ValueError):
            key = str(bst_id)
        return key in existing_ids or not key.isdigit() or int(key) >= 900000

    chosen: dict[str, dict[str, object]] = {}
    for entry in entries:
        eva = entry.get("eva_nr")
        if not isinstance(eva, str) or not eva.strip():
            continue
        current = chosen.get(eva)
        if current is None or (not _known(current) and _known(entry)):
            chosen[eva] = entry
    kept: list[dict[str, object]] = []
    for entry in entries:
        eva = entry.get("eva_nr")
        if isinstance(eva, str) and eva.strip() and chosen[eva] is not entry:
            logger.warning(
                "Skipping %s (bst_id=%s): EVA %s already belongs to %s",
                sanitize_log_arg(str(entry.get("name"))),
                sanitize_log_arg(str(entry.get("bst_id"))),
                sanitize_log_arg(eva),
                sanitize_log_arg(str(chosen[eva].get("name"))),
            )
            continue
        kept.append(entry)
    return kept


def _filter_relevant_stations(stations: list[Station]) -> list[Station]:
    filtered = [station for station in stations if station.in_vienna or station.pendler]
    removed = len(stations) - len(filtered)
    if removed:
        logger.info("Dropping %d stations outside Vienna and commuter belt", removed)
    return filtered
