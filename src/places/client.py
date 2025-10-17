"""Client abstraction for the Google Places API (New)."""

from __future__ import annotations

import logging
import os
import random
import time
from dataclasses import dataclass
from typing import Dict, Iterable, Iterator, List, Optional

import requests

from .tiling import Tile

__all__ = [
    "GooglePlacesClient",
    "GooglePlacesConfig",
    "GooglePlacesError",
    "GooglePlacesTileError",
    "Place",
    "get_places_api_key",
]

LOGGER = logging.getLogger("places.google")

_FIELD_MASK = (
    "places.id,places.displayName,places.location,places.types," "places.formattedAddress,nextPageToken"
)
_API_BASE = "https://places.googleapis.com/v1"


class GooglePlacesError(RuntimeError):
    """Raised for unrecoverable errors when talking to Google Places."""


class GooglePlacesTileError(GooglePlacesError):
    """Raised when a particular tile cannot be processed."""


@dataclass(frozen=True)
class Place:
    """Representation of a place returned by the API."""

    place_id: str
    name: str
    latitude: float
    longitude: float
    types: List[str]
    formatted_address: Optional[str]


@dataclass(frozen=True)
class GooglePlacesConfig:
    api_key: str
    included_types: List[str]
    language: str
    region: str
    radius_m: int
    timeout_s: float
    max_retries: int


class GooglePlacesClient:
    """Lightweight API client with retry and pagination support."""

    def __init__(
        self,
        config: GooglePlacesConfig,
        *,
        session: Optional[requests.Session] = None,
    ) -> None:
        self._config = config
        self._session = session or requests.Session()
        self.request_count = 0

    def iter_nearby(self, tiles: Iterable[Tile]) -> Iterator[Place]:
        for tile in tiles:
            yield from self._iter_tile(tile)

    def _iter_tile(self, tile: Tile) -> Iterator[Place]:
        base_body: Dict[str, object] = {
            "languageCode": self._config.language,
            "regionCode": self._config.region,
            "includedTypes": self._config.included_types,
            "locationRestriction": {
                "circle": {
                    "center": {
                        "latitude": tile.latitude,
                        "longitude": tile.longitude,
                    },
                    "radius": self._config.radius_m,
                }
            },
        }

        page_token: Optional[str] = None
        while True:
            body = dict(base_body)
            if page_token:
                body["pageToken"] = page_token
            try:
                response = self._post("places:searchNearby", body)
            except GooglePlacesError:
                raise
            except Exception as exc:  # pragma: no cover - defensive
                raise GooglePlacesTileError(
                    f"Unexpected error while fetching tile {tile!r}: {exc}"
                ) from exc

            places = response.get("places", [])
            if not isinstance(places, list):
                raise GooglePlacesTileError(
                    f"Unexpected payload for tile {tile!r}: {response!r}"
                )

            for raw in places:
                place = self._parse_place(raw)
                if place is not None:
                    yield place

            page_token = response.get("nextPageToken")
            if not page_token:
                break

    def _parse_place(self, raw: object) -> Optional[Place]:
        if not isinstance(raw, dict):
            LOGGER.warning("Ignoring unexpected place payload: %r", raw)
            return None
        place_id = raw.get("id")
        if not isinstance(place_id, str):
            LOGGER.warning("Skipping place without valid id: %r", raw)
            return None
        display_name = raw.get("displayName")
        if isinstance(display_name, dict):
            name = display_name.get("text")
        else:
            name = None
        if not isinstance(name, str):
            LOGGER.warning("Skipping place without valid name: %s", place_id)
            return None
        location = raw.get("location")
        if not isinstance(location, dict):
            LOGGER.warning("Skipping place without location: %s", place_id)
            return None
        latitude = location.get("latitude")
        longitude = location.get("longitude")
        if not isinstance(latitude, (float, int)) or not isinstance(longitude, (float, int)):
            LOGGER.warning("Skipping place with invalid coordinates: %s", place_id)
            return None
        types_raw = raw.get("types")
        types: List[str]
        if isinstance(types_raw, list):
            types = [str(item) for item in types_raw if isinstance(item, str)]
        else:
            types = []
        formatted_address = raw.get("formattedAddress")
        if not isinstance(formatted_address, str):
            formatted_address = None
        return Place(
            place_id=place_id,
            name=name,
            latitude=float(latitude),
            longitude=float(longitude),
            types=types,
            formatted_address=formatted_address,
        )

    def _post(self, endpoint: str, body: Dict[str, object]) -> Dict[str, object]:
        url = f"{_API_BASE}/{endpoint}"
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self._config.api_key,
            "X-Goog-FieldMask": _FIELD_MASK,
        }
        attempt = 0
        last_error: Optional[Exception] = None
        while attempt <= self._config.max_retries:
            attempt += 1
            try:
                response = self._session.post(
                    url,
                    headers=headers,
                    json=body,
                    timeout=self._config.timeout_s,
                )
                self.request_count += 1
            except requests.RequestException as exc:
                last_error = exc
                LOGGER.warning("Request error (attempt %s/%s): %s", attempt, self._config.max_retries + 1, exc)
            else:
                if response.status_code == 200:
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise GooglePlacesError(
                            "Invalid JSON payload received from Places API"
                        ) from exc
                if response.status_code in {429, 500, 502, 503, 504}:
                    last_error = GooglePlacesError(
                        f"HTTP {response.status_code}: {response.text[:200]}"
                    )
                else:
                    raise GooglePlacesError(
                        f"Request failed with status {response.status_code}: {response.text[:200]}"
                    )

            if attempt > self._config.max_retries:
                break
            sleep_for = self._backoff(attempt)
            LOGGER.info("Retrying %s in %.2fs", endpoint, sleep_for)
            time.sleep(sleep_for)

        if last_error is None:
            raise GooglePlacesError("Unknown error during Places API call")
        if isinstance(last_error, GooglePlacesError):
            raise last_error
        raise GooglePlacesError(str(last_error)) from last_error

    def _backoff(self, attempt: int) -> float:
        base = 0.5 * (2 ** (attempt - 1))
        jitter = random.uniform(0, 0.5)
        return base + jitter


def get_places_api_key() -> str:
    """Return the configured Google Places API key.

    Preference is given to ``GOOGLE_ACCESS_ID`` for forward compatibility. The
    deprecated ``GOOGLE_MAPS_API_KEY`` is retained as a fallback and emits a
    warning when used. The function aborts the program with ``SystemExit``
    (status code ``2``) when neither variable is defined.
    """

    env = os.environ
    access_id = env.get("GOOGLE_ACCESS_ID")
    if access_id:
        key = access_id.strip()
        if key:
            return key

    legacy_key = env.get("GOOGLE_MAPS_API_KEY")
    if legacy_key:
        key = legacy_key.strip()
        if key:
            LOGGER.warning(
                "DEPRECATED: use GOOGLE_ACCESS_ID instead of GOOGLE_MAPS_API_KEY"
            )
            return key

    message = "Missing GOOGLE_ACCESS_ID (preferred) or GOOGLE_MAPS_API_KEY."
    LOGGER.error(message)
    exc = SystemExit(2)
    exc.args = (message,)
    raise exc
