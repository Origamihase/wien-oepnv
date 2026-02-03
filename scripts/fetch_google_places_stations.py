#!/usr/bin/env python3
"""Fetch stations from the Google Places API and merge into stations.json.

Requires the ``GOOGLE_ACCESS_ID`` environment variable (preferred) or the
deprecated ``GOOGLE_MAPS_API_KEY`` as a fallback.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Iterable, List, MutableMapping, Optional, Sequence

# When executed as ``python scripts/fetch_google_places_stations.py`` the parent
# directory (repository root) is not on ``sys.path`` which prevents importing
# the ``src`` package. Ensure the root is available before performing the
# imports below.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.places.client import (
    GooglePlacesClient,
    GooglePlacesConfig,
    GooglePlacesError,
    GooglePlacesPermissionError,
    GooglePlacesTileError,
    Place,
    DEFAULT_INCLUDED_TYPES,
    MAX_RESULTS,
    RADIUS_M,
    get_places_api_key,
)
from src.places.quota import (
    MonthlyQuota,
    QuotaConfig,
    load_quota_config_from_env,
    resolve_quota_state_path,
)
from src.places.diagnostics import permission_hint
from src.places.merge import BoundingBox, MergeConfig, merge_places, load_stations
from src.places.tiling import Tile, iter_tiles, load_tiles_from_env, load_tiles_from_file
from src.utils.env import load_default_env_files
from src.utils.files import atomic_write

LOGGER = logging.getLogger("places.cli")


@dataclass(frozen=True)
class RuntimeConfig:
    client_config: GooglePlacesConfig
    merge_config: MergeConfig
    output_path: Path
    tiles: List[Tile]
    dump_path: Optional[Path]
    dry_run: bool
    write: bool
    quota_config: QuotaConfig
    quota_state_path: Path
    enforce_free_cap: bool


def _configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "Authentication: set GOOGLE_ACCESS_ID (preferred). The legacy "
            "GOOGLE_MAPS_API_KEY remains supported but is deprecated."
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="Print diff without writing")
    parser.add_argument("--write", action="store_true", help="Persist merged stations.json")
    parser.add_argument(
        "--tiles-file",
        type=Path,
        help="JSON file containing tile centres overriding PLACES_TILES",
    )
    parser.add_argument(
        "--dump-new",
        type=Path,
        help="Optional path to write new or updated entries",
    )
    parser.add_argument(
        "--enforce-free-cap",
        dest="enforce_free_cap",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable/disable enforcement of the configured free quota cap (default: enabled)",
    )
    return parser.parse_args(argv)


def _parse_included_types(raw: str | None) -> List[str]:
    if raw is None:
        return list(DEFAULT_INCLUDED_TYPES)
    items = [part.strip() for part in raw.split(",") if part.strip()]
    if not items:
        return list(DEFAULT_INCLUDED_TYPES)
    return items


def _parse_tiles(args: argparse.Namespace, env: MutableMapping[str, str]) -> List[Tile]:
    if args.tiles_file is not None:
        return load_tiles_from_file(args.tiles_file)
    return load_tiles_from_env(env.get("PLACES_TILES"))


def _parse_bounding_box(raw: str | None) -> Optional[BoundingBox]:
    if not raw:
        return None
    data = json.loads(raw)
    try:
        return BoundingBox(
            min_lat=float(data["min_lat"]),
            min_lng=float(data["min_lng"]),
            max_lat=float(data["max_lat"]),
            max_lng=float(data["max_lng"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("BOUNDINGBOX_VIENNA must define min_lat/min_lng/max_lat/max_lng") from exc


def _build_runtime_config(args: argparse.Namespace) -> RuntimeConfig:
    load_default_env_files()
    env = os.environ

    if args.dry_run and args.write:
        raise ValueError("--dry-run and --write are mutually exclusive")

    api_key = get_places_api_key()
    included_types = _parse_included_types(env.get("PLACES_INCLUDED_TYPES"))
    language = env.get("PLACES_LANGUAGE", "de")
    region = env.get("PLACES_REGION", "AT")
    timeout_s = float(env.get("REQUEST_TIMEOUT_S", "25"))
    max_retries = int(env.get("REQUEST_MAX_RETRIES", "4"))

    tiles = _parse_tiles(args, env)

    merge_distance = float(env.get("MERGE_MAX_DIST_M", "150"))
    bounding_box = _parse_bounding_box(env.get("BOUNDINGBOX_VIENNA"))

    out_path = Path(env.get("OUT_PATH_STATIONS", "data/stations.json"))

    client_config = GooglePlacesConfig(
        api_key=api_key,
        included_types=included_types,
        language=language,
        region=region,
        radius_m=RADIUS_M,
        timeout_s=timeout_s,
        max_retries=max_retries,
        max_result_count=MAX_RESULTS,
    )
    merge_config = MergeConfig(max_distance_m=merge_distance, bounding_box=bounding_box)
    quota_config = load_quota_config_from_env(env)
    quota_state_path = resolve_quota_state_path(env)

    return RuntimeConfig(
        client_config=client_config,
        merge_config=merge_config,
        output_path=out_path,
        tiles=tiles,
        dump_path=args.dump_new,
        dry_run=args.dry_run,
        write=args.write,
        quota_config=quota_config,
        quota_state_path=quota_state_path,
        enforce_free_cap=args.enforce_free_cap,
    )


def _fetch_places(client: GooglePlacesClient, tiles: Iterable[Tile]) -> List[Place]:
    places_by_id: dict[str, Place] = {}
    for tile in iter_tiles(tiles):
        LOGGER.info("Fetching tile at %.5f/%.5f", tile.latitude, tile.longitude)
        try:
            for place in client.iter_nearby([tile]):
                places_by_id.setdefault(place.place_id, place)
        except GooglePlacesTileError as exc:
            LOGGER.warning("Skipping tile %.5f/%.5f due to error: %s", tile.latitude, tile.longitude, exc)
            continue
    return list(places_by_id.values())


def _print_diff(
    new_entries: Sequence[MutableMapping[str, object]],
    updated_entries: Sequence[MutableMapping[str, object]],
    skipped: Sequence[Place],
) -> None:
    if new_entries:
        LOGGER.info("New stations (%d):", len(new_entries))
        for entry in sorted(new_entries, key=lambda item: str(item.get("name", ""))):
            LOGGER.info("  + %s", entry.get("name"))
    if updated_entries:
        LOGGER.info("Updated stations (%d):", len(updated_entries))
        for entry in sorted(updated_entries, key=lambda item: str(item.get("name", ""))):
            LOGGER.info("  ~ %s", entry.get("name"))
    if skipped:
        LOGGER.info("Skipped places already covered (%d)", len(skipped))


def _permission_hint(details: str) -> Optional[str]:
    message = details.lower()

    if "are blocked" in message or "blocked" in message:
        return (
            "Check the Google Cloud project: enable Places API (New) and allow the API key to call "
            "https://places.googleapis.com in its API restrictions."
        )

    if "api key" in message and "invalid" in message:
        return (
            "The configured GOOGLE_ACCESS_ID does not look like a valid Maps API key. "
            "Provide a key that starts with 'AIza' or update the secret."
        )

    if "ip" in message and "not authorized" in message:
        return "Update the API key restrictions to allow requests from GitHub Actions IP ranges."

    if "service has been disabled" in message or "api has not been used" in message:
        return "Enable Places API (New) in the Google Cloud console for the project tied to the API key."

    return None


def _dump_changes(
    path: Path,
    new_entries: Sequence[MutableMapping[str, object]],
    updated_entries: Sequence[MutableMapping[str, object]],
) -> None:
    payload = json.dumps(
        {
            "new": list(new_entries),
            "updated": list(updated_entries),
        },
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    path.write_text(payload + "\n", encoding="utf-8")
    LOGGER.info("Wrote change dump to %s", path)


def _write_if_changed(path: Path, stations: Sequence[MutableMapping[str, object]]) -> None:
    payload = json.dumps({"stations": list(stations)}, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.exists():
        current = path.read_text(encoding="utf-8")
        if current == payload:
            LOGGER.info("Stations file already up-to-date")
            return
    path.parent.mkdir(parents=True, exist_ok=True)
    if not payload.strip():
        LOGGER.warning("Refusing to write empty stations payload to %s", path)
        return

    with atomic_write(path, mode="w", encoding="utf-8", permissions=0o644) as handle:
        handle.write(payload)

    LOGGER.info("Wrote stations to %s", path)


def _log_quota_status(quota: MonthlyQuota, config: QuotaConfig) -> None:
    limits = {
        "total": config.limit_total,
        "nearby": config.limit_nearby,
        "text": config.limit_text,
        "details": config.limit_details,
    }
    counts = {key: quota.counts.get(key, 0) for key in ("nearby", "text", "details")}
    LOGGER.info(
        "Quota status for %s: total=%d/%s nearby=%d/%s text=%d/%s details=%d/%s",
        quota.month_key,
        quota.total,
        _format_limit(limits["total"]),
        counts["nearby"],
        _format_limit(limits["nearby"]),
        counts["text"],
        _format_limit(limits["text"]),
        counts["details"],
        _format_limit(limits["details"]),
    )


def _format_limit(value: int | None) -> str:
    return str(value) if value is not None else "∞"


def main(argv: Sequence[str] | None = None) -> int:
    _configure_logging()
    try:
        args = _parse_args(argv)
        runtime = _build_runtime_config(args)
    except Exception as exc:
        LOGGER.error("Configuration error: %s", exc)
        return 2

    try:
        quota = MonthlyQuota.load(runtime.quota_state_path)
    except Exception as exc:
        LOGGER.error("Failed to load quota state: %s", exc)
        return 1

    if quota.maybe_reset_month():
        try:
            quota.save_atomic(runtime.quota_state_path)
        except OSError as exc:
            LOGGER.error("Failed to persist quota reset: %s", exc)
            return 1

    _log_quota_status(quota, runtime.quota_config)

    client = GooglePlacesClient(
        runtime.client_config,
        quota=quota,
        quota_config=runtime.quota_config,
        quota_state_path=runtime.quota_state_path,
        enforce_quota=runtime.enforce_free_cap,
    )
    try:
        places = _fetch_places(client, runtime.tiles)
    except GooglePlacesPermissionError as exc:
        LOGGER.error("Places API access denied: %s", exc)
        hint = permission_hint(str(exc))
        if hint:
            LOGGER.error(hint)
        else:
            LOGGER.error(
                "Skipping Places update. Ensure the configured API key has access to places.googleapis.com"
            )
        return 0
    except GooglePlacesError as exc:
        LOGGER.error("Failed to fetch places: %s", exc)
        return 1

    LOGGER.info("Fetched %d places using %d requests", len(places), client.request_count)

    if runtime.enforce_free_cap and client.quota_skipped_kinds:
        LOGGER.warning("Quota reached, using existing cache. No files were modified.")
        return 0

    stations_path = runtime.output_path
    try:
        existing = load_stations(stations_path)
    except Exception as exc:
        LOGGER.error("Failed to read existing stations: %s", exc)
        return 1

    outcome = merge_places(existing, places, runtime.merge_config)
    LOGGER.info(
        "Stations before: %d, after: %d, new: %d, updated: %d, skipped: %d",
        len(existing),
        len(outcome.stations),
        len(outcome.new_entries),
        len(outcome.updated_entries),
        len(outcome.skipped_places),
    )

    _print_diff(outcome.new_entries, outcome.updated_entries, outcome.skipped_places)

    if runtime.dump_path:
        _dump_changes(runtime.dump_path, outcome.new_entries, outcome.updated_entries)

    if runtime.write:
        _write_if_changed(stations_path, outcome.stations)
    elif runtime.dry_run:
        _log_quota_status(quota, runtime.quota_config)
        LOGGER.info("Dry-run completed; no files written")
    else:
        LOGGER.info("No output written (use --write to persist changes)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
