#!/usr/bin/env python3
"""Verify that the configured Google Places API key can perform requests."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import MutableMapping, Optional, Sequence, Tuple

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.places.client import (
    GooglePlacesClient,
    GooglePlacesConfig,
    GooglePlacesError,
    GooglePlacesPermissionError,
    Place,
    get_places_api_key,
)
from src.places.diagnostics import permission_hint
from src.places.tiling import Tile, load_tiles_from_env
from src.utils.env import load_default_env_files

LOGGER = logging.getLogger("places.verify")


def _configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def _parse_included_types(raw: str | None) -> list[str]:
    raw_value = raw or "train_station,subway_station,bus_station"
    items = [part.strip() for part in raw_value.split(",") if part.strip()]
    if not items:
        raise ValueError("PLACES_INCLUDED_TYPES must not be empty")
    return items


def _build_config(env: MutableMapping[str, str]) -> Tuple[GooglePlacesConfig, Tile]:
    api_key = get_places_api_key()
    included_types = _parse_included_types(env.get("PLACES_INCLUDED_TYPES"))
    language = env.get("PLACES_LANGUAGE", "de")
    region = env.get("PLACES_REGION", "AT")
    radius_m = int(env.get("PLACES_RADIUS_M", "2500"))
    timeout_s = float(env.get("REQUEST_TIMEOUT_S", "25"))
    max_retries = int(env.get("REQUEST_MAX_RETRIES", "4"))

    tiles = load_tiles_from_env(env.get("PLACES_TILES"))
    if not tiles:
        raise ValueError("No tiles available for verification")
    probe_tile = tiles[0]

    config = GooglePlacesConfig(
        api_key=api_key,
        included_types=included_types,
        language=language,
        region=region,
        radius_m=radius_m,
        timeout_s=timeout_s,
        max_retries=max_retries,
    )
    return config, probe_tile


def _verify_access(
    config: GooglePlacesConfig,
    tile: Tile,
    *,
    client: Optional[GooglePlacesClient] = None,
) -> Tuple[Optional[Place], int]:
    probe_client = client or GooglePlacesClient(config)
    iterator = probe_client.iter_nearby([tile])
    try:
        first_place = next(iterator)
    except StopIteration:
        first_place = None
    return first_place, probe_client.request_count


def main(argv: Sequence[str] | None = None) -> int:
    del argv  # Unused but kept for consistency with other scripts.
    _configure_logging()
    env = os.environ
    load_default_env_files(environ=env)

    try:
        config, probe_tile = _build_config(env)
    except SystemExit as exc:
        LOGGER.error("Missing Places API credentials: %s", exc)
        return 2
    except Exception as exc:
        LOGGER.error("Configuration error: %s", exc)
        return 2

    client = GooglePlacesClient(config)

    try:
        first_place, request_count = _verify_access(config, probe_tile, client=client)
    except GooglePlacesPermissionError as exc:
        LOGGER.error("Places API denied the request: %s", exc)
        hint = permission_hint(str(exc))
        if hint:
            LOGGER.error(hint)
        return 1
    except GooglePlacesError as exc:
        LOGGER.error("Places API request failed: %s", exc)
        return 1
    except Exception as exc:  # pragma: no cover - defensive
        LOGGER.error("Unexpected error while verifying Places API access: %s", exc)
        return 1

    if first_place is not None:
        LOGGER.info(
            "Places API access verified; received place %s (%s)",
            first_place.name,
            first_place.place_id,
        )
    else:
        LOGGER.info("Places API access verified; request completed with zero results.")

    LOGGER.info("Verification used %d request(s).", request_count)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
