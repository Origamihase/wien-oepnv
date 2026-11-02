#!/usr/bin/env python3
"""Download and parse the ÖBB station directory Excel file.

The script exports a simplified JSON mapping (``bst_id``, ``bst_code``, ``name``,
``in_vienna`` and ``pendler``) that is used throughout the project. Station names
are harmonized with the previous export, geodata is used to flag Vienna
locations and commuter belt entries, and stations outside this area are omitted.
The data is obtained from the official ÖBB Open-Data portal.

The building blocks (data model, CSV/JSON loaders, workbook download and
parsing, VOR matching, Vienna/pendler flags and coordinate enrichment) live in
``src/station_directory/``; this script keeps the CLI, the provider cache
refresh, the writer and ``main`` and re-exports the moved names.
"""

from __future__ import annotations

import argparse
import json
import logging

# Bandit B404: subprocess is required to invoke internal cache-refresh
# scripts. Inputs are static lists, never user-supplied.
import subprocess  # nosec B404
import sys
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable, Sequence

# ``subprocess`` is re-exported on purpose (NOT a public API of this script):
# ``tests/test_update_station_directory_cache_refresh.py`` patches
# ``usd.subprocess.run`` and mypy --strict's ``no_implicit_reexport`` requires
# the name to be in ``__all__`` for that attribute access to type-check.
# The names imported from ``src.station_directory`` are listed for the same
# reason: they used to be defined here and callers/tests still read them as
# ``usd.<name>``.
__all__ = [
    "subprocess",
    "DEFAULT_CACHED_WORKBOOK_PATH",
    "LocationInfo",
    "Station",
    "VORStop",
    "_IN_VIENNA_CACHE_KEY",
    "_annotate_station_flags",
    "_assign_vor_ids",
    "_build_location_index",
    "_coerce_bst_id",
    "_drop_duplicate_eva",
    "_enrich_manual_stations",
    "_enrich_with_geonetz",
    "_enrich_with_hafas",
    "_fill_coordinates_from_geonetz",
    "_filter_relevant_stations",
    "_harmonize_station_names",
    "_load_existing_station_entries",
    "_load_geonetz_stops",
    "_load_gtfs_locations",
    "_load_vor_locations",
    "_load_vor_name_to_id_map",
    "_load_wienerlinien_locations",
    "_merge_sources_into_entry",
    "_resolve_in_vienna_with_cache",
    "_restore_existing_metadata",
    "_stations_missing_coordinates",
    "_wl_diva_key",
    "download_workbook",
    "extract_stations",
    "load_pendler_name_candidates",
    "load_pendler_station_ids",
    "load_vor_stops",
]

DEFAULT_SOURCE_URL = "https://data.oebb.at/dam/jcr:fce22daf-0dd8-4a15-80b4-dbca6e80ce38/" "Verzeichnis%20der%20Verkehrsstationen.xlsx"
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.utils.files import atomic_write, loads_finite  # noqa: E402
from src.utils.serialize import scrub_trojan_source_primitives  # noqa: E402

from src.places.merge import BoundingBox  # noqa: E402
from src.utils.env import _path_fingerprint, get_bool_env, load_default_env_files  # noqa: E402
from src.utils.logging import sanitize_log_arg  # noqa: E402

from src.station_directory.coordinates import (  # noqa: E402
    _enrich_manual_stations,
    _enrich_with_geonetz,
    _enrich_with_hafas,
    _fill_coordinates_from_geonetz,
    _merge_sources_into_entry,
    _stations_missing_coordinates,
)
from src.station_directory.csv_sources import (  # noqa: E402
    _build_location_index,
    _load_gtfs_locations,
    _load_vor_locations,
    _load_wienerlinien_locations,
    load_vor_stops,
)
from src.station_directory.json_sources import (  # noqa: E402
    _load_existing_station_entries,
    _load_geonetz_stops,
    _load_vor_name_to_id_map,
    load_pendler_name_candidates,
    load_pendler_station_ids,
)
from src.station_directory.matching import (  # noqa: E402
    _IN_VIENNA_CACHE_KEY,
    _annotate_station_flags,
    _assign_vor_ids,
    _drop_duplicate_eva,
    _filter_relevant_stations,
    _harmonize_station_names,
    _resolve_in_vienna_with_cache,
    _restore_existing_metadata,
)
from src.station_directory.model import (  # noqa: E402
    LocationInfo,
    Station,
    VORStop,
    _wl_diva_key,
)
from src.station_directory.workbook import (  # noqa: E402
    DEFAULT_CACHED_WORKBOOK_PATH,
    _coerce_bst_id,
    download_workbook,
    extract_stations,
)

DEFAULT_OUTPUT_PATH = _ROOT / "data" / "stations.json"
DEFAULT_PENDLER_PATH = _ROOT / "data" / "pendler_bst_ids.json"
DEFAULT_PENDLER_CANDIDATES_PATH = _ROOT / "data" / "pendler_candidates.json"
DEFAULT_GTFS_STOPS_PATH = _ROOT / "data" / "gtfs" / "stops.txt"
DEFAULT_WL_HALTEPUNKTE_PATH = _ROOT / "data" / "wienerlinien-ogd-haltepunkte.csv"
DEFAULT_VOR_STOPS_PATH = _ROOT / "data" / "vor-haltestellen.csv"
# Compact projection of the ÖBB-Infrastruktur GeoNetz dataset (1 056 rail
# stations with their UIC EVA-Nr, IFOPT-ID and postal address). Generated
# by ``scripts/extract_oebb_geonetz_stops.py`` from the upstream 23 MiB
# ``GeoNetz_12-2024.zip``. Read by ``_enrich_with_geonetz`` to attach
# the three identifier fields onto every ÖBB-Excel station whose
# ``bst_id`` matches a row's ``bsts_id``. Coordinates are left untouched
# by this enrichment — they are governed by the HAFAS tier and the
# PR #1601 GeoNetz-reconciliation; this loader only adds metadata.
DEFAULT_GEONETZ_STOPS_PATH = _ROOT / "data" / "oebb_geonetz_stops.json"


logger = logging.getLogger("update_station_directory")


def _empty_args() -> tuple[str, ...]:
    return ()


@dataclass(frozen=True)
class CacheRefreshTarget:
    label: str
    script_candidates: tuple[str, ...]
    optional: bool = False
    extra_args_factory: Callable[[], Sequence[str]] = _empty_args
    availability_check: Callable[[], bool] | None = None


_CACHE_REFRESH_TARGETS: tuple[CacheRefreshTarget, ...] = (
    CacheRefreshTarget("ÖBB", ("update_oebb_cache.py",)),
    # VOR was removed from the station-directory refresh path on
    # 2026-05-11 per operator policy: VOR is now used only for the
    # Stammstrecke delay monitor (every 30 min, via update-cycle.yml).
    # The station directory uses the pinned ``data/vor-haltestellen.csv``
    # snapshot for VOR stop IDs.
    CacheRefreshTarget("Wiener Linien", ("update_wl_cache.py",)),
)


def _refresh_provider_caches(*, script_dir: Path | None = None) -> None:
    base_dir = script_dir or Path(__file__).resolve().parent
    for target in _CACHE_REFRESH_TARGETS:
        if target.availability_check and not target.availability_check():
            logger.info("Skipping %s cache refresh (credentials not available)", target.label)
            continue

        command: list[str] | None = None
        script_path: Path | None = None
        for candidate in target.script_candidates:
            candidate_path = base_dir / candidate
            if candidate_path.exists():
                extra_args = list(target.extra_args_factory())
                command = [sys.executable, str(candidate_path), *extra_args]
                script_path = candidate_path
                break

        if command is None or script_path is None:
            message = "No cache refresh script found for %s; skipping" if target.optional else "Cache refresh script missing for %s"
            log_method = logger.debug if target.optional else logger.warning
            log_method(message, target.label)
            continue

        logger.info("Refreshing %s cache via %s", target.label, script_path.name)
        try:
            # Enforce a 5-minute timeout to prevent indefinite hangs (DoS protection)
            result = subprocess.run(command, check=False, shell=False, timeout=300)  # nosec B603
        except subprocess.TimeoutExpired:
            logger.warning("%s cache refresh timed out after 300s; continuing", target.label)
            continue
        except OSError as exc:  # pragma: no cover - execution environment issues
            logger.warning("Failed to execute %s cache refresh (%s); continuing", target.label, exc)
            continue

        if result.returncode != 0:
            logger.warning(
                "%s cache refresh exited with code %s; continuing",
                target.label,
                result.returncode,
            )
        else:
            logger.info("%s cache refresh completed", target.label)


def _parse_float(raw: str | None, *, key: str, default: float) -> float:
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        # Security (Path-Log Sibling Drift Round 4, env-repr closure):
        # ``raw`` is operator-controlled; ``sanitize_log_arg`` strips
        # Variation Selectors and other invisible bytes that ``%r`` lets
        # through before the value lands in the log. ``key`` is a hardcoded module-internal constant
        # (``REQUEST_TIMEOUT_S`` / ``MERGE_MAX_DIST_M``); only ``raw`` is
        # operator-controlled and therefore the only arg requiring the
        # canonical scrub.
        logger.warning(
            "Invalid %s=%s – using default %s",
            key,
            sanitize_log_arg(raw),
            default,
        )
        return default


# No caller since the OSM tier left on 2026-10-17; kept with
# ``_parse_float`` because the sentinel tests pin their input hardening.
def _parse_bounding_box(raw: str | None) -> BoundingBox | None:
    if not raw:
        return None
    # Security: ``RecursionError`` covers JSON depth-bomb attacks in the
    # ``BOUNDINGBOX_VIENNA`` env override (intentional misconfig, leaked
    # CI env, compromised secret store). ``json.loads`` raises
    # ``RecursionError`` (NOT a subclass of ``json.JSONDecodeError`` and
    # NOT caught by the caller's ``except ValueError``) on a
    # deeply-nested payload. Without
    # this catch the unhandled error crashes the entire
    # ``update_station_directory.py`` cron pipeline.
    try:
        # Security: ``loads_finite`` pins parse_constant + parse_float
        # hooks (Round 1503 sibling) that reject NaN / Infinity / 1e1000
        # literals planted into the env-controlled ``BOUNDINGBOX_VIENNA``
        # override (leaked CI env / compromised secret store / hostile
        # operator).
        data = loads_finite(raw)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("BOUNDINGBOX_VIENNA must be valid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("BOUNDINGBOX_VIENNA must define min_lat/min_lng/max_lat/max_lng")
    try:
        return BoundingBox(
            min_lat=float(data["min_lat"]),
            min_lng=float(data["min_lng"]),
            max_lat=float(data["max_lat"]),
            max_lng=float(data["max_lng"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("BOUNDINGBOX_VIENNA must define min_lat/min_lng/max_lat/max_lng") from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download the ÖBB station directory and export a JSON mapping",
    )
    parser.add_argument(
        "--source-url",
        default=DEFAULT_SOURCE_URL,
        help="URL of the Excel workbook to download",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="Path to the JSON file that will be written",
    )
    parser.add_argument(
        "--pendler",
        type=Path,
        metavar="PATH",
        default=DEFAULT_PENDLER_PATH,
        help="Path to the JSON file containing pendler station IDs",
    )
    parser.add_argument(
        "--pendler-candidates",
        type=Path,
        metavar="PATH",
        default=DEFAULT_PENDLER_CANDIDATES_PATH,
        help=(
            "Path to the name-based pendler whitelist "
            "(default: data/pendler_candidates.json). Stations whose ÖBB name "
            "matches a candidate here are also marked pendler=true."
        ),
    )
    parser.add_argument(
        "--vor-stops",
        type=Path,
        metavar="PATH",
        default=DEFAULT_VOR_STOPS_PATH,
        help="Path to the VOR stop CSV used to enrich stations with their vor_id",
    )
    parser.add_argument(
        "--geonetz-stops",
        type=Path,
        metavar="PATH",
        default=DEFAULT_GEONETZ_STOPS_PATH,
        help=(
            "Path to the compact ÖBB-Infrastruktur GeoNetz stops JSON "
            "(produced by scripts/extract_oebb_geonetz_stops.py from the "
            "upstream GeoNetz_12-2024.zip). Used to attach UIC eva_nr, "
            "IFOPT-ID and postal address to oebb-source stations."
        ),
    )
    parser.add_argument(
        "--gtfs-stops",
        type=Path,
        metavar="PATH",
        default=DEFAULT_GTFS_STOPS_PATH,
        help="Path to a GTFS stops.txt file for coordinate lookup",
    )
    parser.add_argument(
        "--wl-haltepunkte",
        type=Path,
        metavar="PATH",
        default=DEFAULT_WL_HALTEPUNKTE_PATH,
        help="Path to the Wiener Linien haltepunkte CSV for coordinate lookup",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print progress information during the update run",
    )
    return parser.parse_args()


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    # Sentinel: route through SafeFormatter so any raw exception text
    # logged via %s in this script is sanitised at the formatter layer.
    from src.feed.logging_safe import setup_script_logging

    setup_script_logging(level)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def write_json(stations_list: list[dict[str, object]], output_path: Path) -> None:
    # Security (Trojan-Source / BiDi-Mark Drift Round 14, ingestion-boundary
    # defence): strip the canonical CVE-2021-42574 attack-byte union from
    # the incoming stations BEFORE ``json.dump``. The OEBB
    # ``Verzeichnis der Verkehrsstationen`` Excel response is the
    # primary delivery vector — a hijacked OGD portal (DNS rebind or
    # cache poisoning) could plant U+202E in a station ``name`` /
    # ``aliases[]`` / ``alternative_names`` field. The weekly
    # ``update-stations.yml`` cron commits ``data/stations.json`` to
    # ``main``. ``ensure_ascii=False`` preserves compact German diffs.
    # Mirrors ``src/places/merge.py:write_stations`` (Round 13).
    #
    # Security (Coordinate finite/range drift, companion-writer
    # defence-in-depth): ``allow_nan=False`` mirrors the canonical
    # writer-side pin established in Round 1485 at
    # ``src/places/merge.py:write_stations``. The local
    # ``_coerce_float_value`` parser (line 455) accepts
    # ``float('nan')`` / ``float('inf')`` from a compromised OGD
    # Wien / GTFS / VOR upstream because the
    # ``isinstance(value, int | float)`` shape check on JSON-decoded
    # numeric values accepts the non-finite literals that
    # ``json.loads`` parses by default. Without this writer-side
    # floor a poisoned upstream silently lands non-standard ``NaN``
    # / ``Infinity`` literals (invalid per RFC 8259) in the public
    # ``data/stations.json`` artefact.
    scrubbed = scrub_trojan_source_primitives(stations_list)
    serialisable = scrubbed if isinstance(scrubbed, list) else stations_list
    payload = {"stations": serialisable}
    # Use atomic_write to prevent partial writes and reduce race conditions.
    with atomic_write(output_path, mode="w", encoding="utf-8", permissions=0o644) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
    logger.info("Wrote [path-sha256=%s]", _path_fingerprint(output_path))


# Refuse to overwrite a populated station directory with a drastically smaller
# ÖBB set (truncated/corrupt workbook). 0.5 → block when the new ÖBB count drops
# below half the existing; ÖBB station counts are very stable, so the false-
# abort risk is negligible while a manual-only / near-empty result is caught.
STATION_DIRECTORY_FLOOR_RATIO = 0.5


def _station_directory_floor_violation(*, new_oebb: int, existing_oebb: int) -> str | None:
    """Return a reason string when writing would lose ÖBB station data, else None.

    A truncated or corrupt ÖBB workbook yields zero or very few ÖBB stations;
    overwriting a populated ``data/stations.json`` with that set silently drops
    the entire ÖBB layer (only the preserved manual entries survive). Mirrors
    the ``write_cache`` degradation guard. A fresh/empty existing directory
    (``existing_oebb == 0``) writes through — there is nothing to lose — but a
    zero-ÖBB result is always rejected because a valid workbook always yields
    stations.
    """
    if new_oebb == 0:
        return "extract_stations produced 0 ÖBB stations (truncated/corrupt workbook?)"
    if existing_oebb and new_oebb < existing_oebb * STATION_DIRECTORY_FLOOR_RATIO:
        return (
            f"{new_oebb} ÖBB stations is below {STATION_DIRECTORY_FLOOR_RATIO:.0%} "
            f"of the existing {existing_oebb} (truncated/corrupt workbook?)"
        )
    return None


def main() -> None:
    args = parse_args()
    configure_logging(args.verbose)
    _refresh_provider_caches()
    existing_entries, manual_stations = _load_existing_station_entries(args.output)
    workbook_stream = download_workbook(args.source_url)
    stations = extract_stations(workbook_stream)
    _harmonize_station_names(stations, existing_entries)
    _restore_existing_metadata(stations, existing_entries)
    pendler_ids = load_pendler_station_ids(path=args.pendler)
    pendler_name_candidates = load_pendler_name_candidates(path=args.pendler_candidates)
    location_index = _build_location_index(
        args.gtfs_stops,
        args.wl_haltepunkte,
        vor_path=args.vor_stops,
    )
    if not location_index:
        logger.warning("No coordinate data available; falling back to name heuristic")
    geonetz_lookup = _load_geonetz_stops(args.geonetz_stops)
    _annotate_station_flags(
        stations,
        pendler_ids,
        location_index,
        pendler_name_candidates=pendler_name_candidates,
        geonetz_lookup=geonetz_lookup,
    )
    vor_stops = load_vor_stops(args.vor_stops) if args.vor_stops else []
    vor_name_map: dict[str, str] = {}
    if args.vor_stops:
        # vor-haltestellen.mapping.json sits next to the .csv (same stem,
        # ``.mapping.json`` suffix) and is produced by fetch_vor_haltestellen.
        mapping_candidate = args.vor_stops.with_suffix(".mapping.json")
        vor_name_map = _load_vor_name_to_id_map(mapping_candidate)
    if vor_stops or vor_name_map:
        _assign_vor_ids(stations, vor_stops, name_to_vor_id=vor_name_map)
    stations = _filter_relevant_stations(stations)

    # GeoNetz metadata enrichment (PR β). Runs after the filter so it
    # only touches the pendler/in_vienna subset, and before HAFAS
    # so downstream enrichment tiers see the eva_nr that PR γ
    # (planned: HAFAS-drift-detection) needs as the join key. Idempotent
    # — re-runs leave previously-set values alone.
    if geonetz_lookup:
        _enrich_with_geonetz(stations, geonetz_lookup)

    # No OpenStreetMap tier (Michael, 17.10.2026): OSM coordinates are
    # under the ODbL, whose share-alike would cover this whole published
    # file, and the file also holds data that cannot be put under the ODbL.
    # Coordinates come from the existing directory (inertia), GeoNetz and
    # the Wiener Linien OGD list; HAFAS fills only what is still missing.
    load_default_env_files()
    if geonetz_lookup:
        _fill_coordinates_from_geonetz(stations, geonetz_lookup)

    # HAFAS (ÖBB Scotty) is the fallback for stations without coordinates.
    # There is no further tier: Michael decided on 2026-10-16 that the
    # station directory holds only openly licensed coordinates (no Google
    # Places). A station still without coordinates stays without them and
    # is named in a warning so the weekly run shows it.
    missing = _stations_missing_coordinates(stations)
    if not missing:
        logger.info(
            "The existing directory already covered all %d stations with coordinates",
            len(stations),
        )
    else:
        missing = _enrich_with_hafas(missing)
        if missing:
            logger.warning(
                "%d stations still without coordinates after HAFAS: %s",
                len(missing),
                ", ".join(sanitize_log_arg(station.name) for station in missing[:20]),
            )
        else:
            logger.info("HAFAS resolved every remaining station")

    # Enrich the manual block (manual_distant_at / manual_foreign_city —
    # the Ostregion Liniennetz stations from PR #1557) that bypassed the
    # ÖBB filter and therefore the ÖBB-side enrichment chain. Re-uses
    # the location_index built earlier (free GTFS/WL/VOR lookup) and falls
    # back to the unmetered HAFAS LocMatch tier — exactly the same
    # cheap-first strategy the ÖBB pipeline already runs.
    #
    # Env-disabled via ``WIEN_OEPNV_MANUAL_ENRICH=0`` — used by
    # ``test_wrapper_atomic_on_success`` to keep the orchestrator
    # subprocess test under its 180-second pytest timeout: the test
    # carries 296 manual entries without coordinates, and 296 real
    # HAFAS LocMatch round-trips from a GitHub-hosted runner regularly
    # burn 3-5 minutes (well over the budget). Production cron runs
    # leave the env unset so the enrichment remains active.
    if get_bool_env("WIEN_OEPNV_MANUAL_ENRICH", True):
        _enrich_manual_stations(manual_stations, location_index)
    else:
        logger.info(
            "Skipping manual entry enrichment (WIEN_OEPNV_MANUAL_ENRICH=0)"
        )

    final_stations = [station.as_dict() for station in stations]
    final_stations.extend(manual_stations)
    final_stations = _drop_duplicate_eva(final_stations, set(existing_entries))

    # Data-loss floor: never replace a populated directory with a truncated ÖBB
    # set (see _station_directory_floor_violation). Fails closed — the pinned
    # committed file stays intact and the cron run surfaces a non-zero exit.
    floor_violation = _station_directory_floor_violation(
        new_oebb=len(stations), existing_oebb=len(existing_entries)
    )
    if floor_violation:
        raise SystemExit(
            "Refusing to overwrite station directory "
            f"[path-sha256={_path_fingerprint(args.output)}]: {floor_violation}"
        )

    write_json(final_stations, args.output)


if __name__ == "__main__":
    main()
