"""HAFAS, manual-entry and GeoNetz coordinate enrichment of the station-directory updater.

Moved verbatim out of ``scripts/update_station_directory.py``, which
re-exports every name it or its tests use.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Mapping

from src.places.hafas_client import enrich_station_with_hafas
from src.station_directory.model import (
    LocationInfo,
    Station,
    _normalize_location_keys,
    _wl_diva_key,
)
from src.utils.logging import sanitize_log_arg

logger = logging.getLogger("update_station_directory")


def _stations_missing_coordinates(stations: list[Station]) -> list[Station]:
    """Return the subset of *stations* that still lack lat/lng metadata.

    HAFAS is only asked for this set, so a cron tick does not send
    hundreds of LocMatch requests when the existing directory already
    covers every station (coordinate inertia).
    """
    missing: list[Station] = []
    for station in stations:
        lat = station.extras.get("latitude")
        lng = station.extras.get("longitude")
        if not isinstance(lat, int | float) or not isinstance(lng, int | float):
            missing.append(station)
    return missing


def _enrich_with_hafas(stations: list[Station]) -> list[Station]:
    """Resolve coordinates for *stations* via the ÖBB HAFAS fallback.

    Iterates over the strict subset of stations still missing
    coordinates and asks
    :func:`src.places.hafas_client.enrich_station_with_hafas` for each.
    The HAFAS hit is committed straight onto the station's extras and the
    HAFAS ``extId`` is persisted as the top-level ``hafas_extId`` key
    (parallel to ``vor_id``) so downstream
    consumers can resolve a station back to its HAFAS identifier
    without re-querying the upstream.

    Returns the residual list of stations *still* missing coordinates
    after HAFAS ran, so the caller can name them in a warning.
    """
    if not stations:
        return []

    updated = 0
    residual: list[Station] = []
    for station in stations:
        hit = enrich_station_with_hafas(station.name)
        if hit is None:
            residual.append(station)
            continue
        station.extras["latitude"] = hit["lat"]
        station.extras["longitude"] = hit["lon"]
        station.extras["hafas_extId"] = hit["extId"]

        existing_source = station.extras.get("source")
        sources: set[str] = set()
        if isinstance(existing_source, str):
            sources.update(s.strip() for s in existing_source.split(",") if s.strip())
        sources.add("hafas")
        station.extras["source"] = ",".join(sorted(sources))
        updated += 1

    logger.info(
        "HAFAS enrichment resolved coordinates for %d of %d stations; %d still missing",
        updated,
        len(stations),
        len(residual),
    )
    return residual


def _enrich_manual_stations(
    manual_stations: list[dict[str, object]],
    location_index: Mapping[str, LocationInfo],
) -> int:
    """Enrich manual entries (``source=manual``, ``type=manual_*``) with
    coordinates from the local lookup index and the HAFAS LocMatch tier.

    Manual entries — added for the Ostregion Liniennetz stations outside
    the Wien/NÖ pendler scope (PR #1557) — bypass ``_filter_relevant_stations``
    and therefore never enter the ÖBB-side HAFAS enrichment.
    Without this step they stay coordinate-less forever, even though the
    cron pipeline already loads sources (GTFS stops.txt, VOR haltestellen.csv)
    and has an unmetered fallback (HAFAS ÖBB Scotty) that can resolve them.

    Resolution order — same fail-cheap-first rationale as
    ``_enrich_with_hafas``:

      1. **Local index** (``location_index`` = GTFS + WL + VOR coords,
         free, in-memory). Already built earlier in ``main()`` for the ÖBB
         flag-annotation pass; we just re-use it.
      2. **HAFAS LocMatch** (ÖBB Scotty, free, no monthly quota; the same
         tier that ``_enrich_with_hafas`` taps for ÖBB stations). Handles
         Austria-wide stations plus most CZ/SK/HU border references that
         Scotty knows.

    Skips entries that already carry ``latitude``/``longitude`` so the
    enrichment is idempotent across cron ticks (first run fills 296 entries;
    subsequent runs no-op unless new manual entries appear).

    Returns the number of entries enriched (for log accounting).
    """
    if not manual_stations:
        return 0

    enriched_local = 0
    enriched_hafas = 0
    still_missing = 0
    skipped_existing = 0

    for entry in manual_stations:
        lat = entry.get("latitude")
        lon = entry.get("longitude")
        if isinstance(lat, int | float) and isinstance(lon, int | float):
            skipped_existing += 1
            continue

        name_raw = entry.get("name")
        if not isinstance(name_raw, str) or not name_raw.strip():
            continue
        name = name_raw.strip()

        # 1. Local index. Try the authoritative WL DIVA first (exact key,
        #    immune to the "Wien <name> (WL)" vs "<name>" mismatch), then
        #    fall back to the normalized name keys.
        info: LocationInfo | None = None
        wl_diva = entry.get("wl_diva")
        if isinstance(wl_diva, str) and wl_diva.strip():
            info = location_index.get(_wl_diva_key(wl_diva.strip()))
        if info is None:
            for key in _normalize_location_keys(name):
                info = location_index.get(key)
                if info:
                    break
        if info is not None:
            entry["latitude"] = info.latitude
            entry["longitude"] = info.longitude
            _merge_sources_into_entry(entry, info.sources)
            enriched_local += 1
            continue

        # 2. HAFAS LocMatch fallback (ÖBB Scotty, free)
        try:
            hit = enrich_station_with_hafas(name)
        except Exception:  # nosec B902 - HAFAS must never crash the pipeline
            logger.warning(
                "HAFAS lookup raised for manual entry %s",
                sanitize_log_arg(name),
            )
            still_missing += 1
            continue

        if hit is None:
            still_missing += 1
            continue

        entry["latitude"] = hit["lat"]
        entry["longitude"] = hit["lon"]
        ext_id = hit.get("extId")
        if isinstance(ext_id, str) and ext_id.strip():
            entry["hafas_extId"] = ext_id.strip()
        _merge_sources_into_entry(entry, {"hafas"})
        enriched_hafas += 1

    logger.info(
        "Manual enrichment: %d via local index, %d via HAFAS, %d already had coords, %d still missing",
        enriched_local,
        enriched_hafas,
        skipped_existing,
        still_missing,
    )
    return enriched_local + enriched_hafas


def _merge_sources_into_entry(entry: dict[str, object], add: Iterable[str]) -> None:
    """Add provider tokens to the entry's comma-separated ``source`` field.

    Preserves the existing tokens (typically ``manual``), adds the new ones,
    deduplicates, and re-emits in alphabetical order — matching the
    canonical format pinned in ``src/places/merge.py`` and validated by
    ``_find_naming_issues`` in ``src/utils/stations_validation.py``.
    """
    raw = entry.get("source")
    tokens: set[str] = set()
    if isinstance(raw, str):
        tokens.update(part.strip() for part in raw.split(",") if part.strip())
    elif isinstance(raw, list):
        tokens.update(str(part).strip() for part in raw if str(part).strip())
    for token in add:
        token = str(token).strip()
        if token:
            tokens.add(token)
    entry["source"] = ",".join(sorted(tokens))


def _enrich_with_geonetz(
    stations: Iterable[Station], geonetz_lookup: Mapping[str, Mapping[str, object]]
) -> None:
    """Attach UIC ``eva_nr``, ``ifopt_id`` and ``address`` from GeoNetz.

    Each station is matched two ways:

    1. **Primary join** by ÖBB ``bst_id`` against the GeoNetz
       ``bsts_id`` column. This covers all 147 oebb-source stations
       cleanly.
    2. **Secondary join** by exact canonical name for entries that
       lack a bst_id match (typically synthetic 900xxx-ids from the
       VOR/WL pathway whose corresponding station nevertheless lives
       in GeoNetz, e.g. Wien Hauptbahnhof, bst_id=900100 in our
       directory vs. BSTS_ID=2393 in GeoNetz). The name match uses
       the unique-name subset of the lookup to avoid false positives
       on the small number of repeated operational names.

    On a hit, the three identifier fields are written to
    ``station.extras`` if not already set. Coordinates are explicitly
    *not* overwritten — they're governed by the HAFAS tier and the
    PR #1601 GeoNetz-reconciliation; this
    loader only adds metadata so the next cron tick doesn't silently
    revert a hand-curated value.

    Idempotent: stations that already carry an ``eva_nr`` from a
    previous run pass through untouched.
    """
    if not geonetz_lookup:
        return
    # Build the secondary by-name index from the same lookup — keep
    # only names that appear exactly once so a duplicate operational
    # name (rare, but happens) can never mis-match.
    name_counts: dict[str, int] = {}
    for row in geonetz_lookup.values():
        nm = row.get("name")
        if isinstance(nm, str) and nm:
            name_counts[nm] = name_counts.get(nm, 0) + 1
    by_name: dict[str, Mapping[str, object]] = {
        str(row["name"]): row
        for row in geonetz_lookup.values()
        if isinstance(row.get("name"), str)
        and name_counts.get(str(row["name"]), 0) == 1
    }
    enriched = 0
    for station in stations:
        geo: Mapping[str, object] | None = None
        bst_id = station.bst_id
        if bst_id:
            try:
                lookup_id = str(int(float(bst_id)))
            except (TypeError, ValueError):
                lookup_id = str(bst_id)
            geo = geonetz_lookup.get(lookup_id)
        if geo is None and station.name:
            geo = by_name.get(station.name)
        if geo is None:
            continue
        changed = False
        for geo_field in ("eva_nr", "ifopt_id", "address"):
            value = geo.get(geo_field)
            if not isinstance(value, str) or not value:
                continue
            if station.extras.get(geo_field):
                continue  # preserve any pre-existing value across cron runs
            station.extras[geo_field] = value
            changed = True
        if changed:
            enriched += 1
            # Append the ``oebb_geonetz`` provenance token to the
            # source list so downstream consumers see the data
            # lineage. Mirrors the convention the PR #1601 coord-
            # reconciliation already set up.
            existing_source = station.extras.get("source")
            tokens: set[str] = set()
            if isinstance(existing_source, str):
                tokens.update(t.strip() for t in existing_source.split(",") if t.strip())
            tokens.add("oebb_geonetz")
            station.extras["source"] = ",".join(sorted(tokens))
    if enriched:
        logger.info("GeoNetz-enriched %d stations with eva_nr/ifopt_id/address", enriched)


def _fill_coordinates_from_geonetz(
    stations: Iterable[Station], geonetz_lookup: Mapping[str, Mapping[str, object]]
) -> int:
    """Give stations without coordinates the GeoNetz point of their ``bst_id``.

    Runs before the HAFAS fallback, so a station of the ÖBB workbook gets an
    openly licensed coordinate (ÖBB-Infrastruktur GeoNetz, CC BY 3.0 AT)
    first; this took over the gap-filling of the OpenStreetMap tier removed
    on 2026-10-17. Only the ``bst_id`` join is used, never the name: a name
    can belong to another place (``Siebenhirten`` bst_id 1371 lies near
    Mistelbach, the Wiener Linien stop of that name in Liesing). Existing
    coordinates are never touched. Returns the number of stations filled.
    """
    filled = 0
    for station in stations:
        lat = station.extras.get("latitude")
        lng = station.extras.get("longitude")
        if isinstance(lat, int | float) and isinstance(lng, int | float):
            continue
        if not station.bst_id:
            continue
        try:
            lookup_id = str(int(float(station.bst_id)))
        except (TypeError, ValueError):
            lookup_id = str(station.bst_id)
        geo = geonetz_lookup.get(lookup_id)
        if geo is None:
            continue
        geo_lat, geo_lon = geo.get("lat"), geo.get("lon")
        if isinstance(geo_lat, bool) or isinstance(geo_lon, bool):
            continue
        if not (isinstance(geo_lat, int | float) and isinstance(geo_lon, int | float)):
            continue
        if not (math.isfinite(geo_lat) and math.isfinite(geo_lon)):
            continue
        if not (-90.0 <= geo_lat <= 90.0 and -180.0 <= geo_lon <= 180.0):
            continue
        station.extras["latitude"] = float(geo_lat)
        station.extras["longitude"] = float(geo_lon)
        existing_source = station.extras.get("source")
        tokens: set[str] = set()
        if isinstance(existing_source, str):
            tokens.update(t.strip() for t in existing_source.split(",") if t.strip())
        tokens.add("oebb_geonetz")
        station.extras["source"] = ",".join(sorted(tokens))
        filled += 1
    if filled:
        logger.info("GeoNetz filled coordinates for %d stations", filled)
    return filled
