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
from typing import Iterable, List, MutableMapping, Optional, Sequence

from src.places.client import (
    GooglePlacesClient,
    GooglePlacesConfig,
    GooglePlacesError,
    GooglePlacesTileError,
    Place,
    get_places_api_key,
)
from src.places.merge import BoundingBox, MergeConfig, merge_places, load_stations
from src.places.tiling import Tile, iter_tiles, load_tiles_from_env, load_tiles_from_file
from src.utils.env import load_default_env_files

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
    return parser.parse_args(argv)


def _parse_included_types(raw: str | None) -> List[str]:
    raw_value = raw or "train_station,subway_station,transit_station"
    items = [part.strip() for part in raw_value.split(",") if part.strip()]
    if not items:
        raise ValueError("PLACES_INCLUDED_TYPES must not be empty")
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
    radius_m = int(env.get("PLACES_RADIUS_M", "2500"))
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
        radius_m=radius_m,
        timeout_s=timeout_s,
        max_retries=max_retries,
    )
    merge_config = MergeConfig(max_distance_m=merge_distance, bounding_box=bounding_box)

    return RuntimeConfig(
        client_config=client_config,
        merge_config=merge_config,
        output_path=out_path,
        tiles=tiles,
        dump_path=args.dump_new,
        dry_run=args.dry_run,
        write=args.write,
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
    payload = json.dumps(list(stations), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.exists():
        current = path.read_text(encoding="utf-8")
        if current == payload:
            LOGGER.info("Stations file already up-to-date")
            return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    LOGGER.info("Wrote stations to %s", path)


def main(argv: Sequence[str] | None = None) -> int:
    _configure_logging()
    try:
        args = _parse_args(argv)
        runtime = _build_runtime_config(args)
    except Exception as exc:
        LOGGER.error("Configuration error: %s", exc)
        return 2

    client = GooglePlacesClient(runtime.client_config)
    try:
        places = _fetch_places(client, runtime.tiles)
    except GooglePlacesError as exc:
        LOGGER.error("Failed to fetch places: %s", exc)
        return 1

    LOGGER.info("Fetched %d places using %d requests", len(places), client.request_count)

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
        LOGGER.info("Dry-run completed; no files written")
    else:
        LOGGER.info("No output written (use --write to persist changes)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
