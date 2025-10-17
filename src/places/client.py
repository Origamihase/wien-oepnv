"""Client abstraction for the Google Places API (New)."""

from __future__ import annotations

import logging
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Set, cast

import requests

from .quota import MonthlyQuota, QuotaConfig
from .tiling import Tile

__all__ = [
    "GooglePlacesClient",
    "GooglePlacesConfig",
    "GooglePlacesError",
    "GooglePlacesPermissionError",
    "GooglePlacesTileError",
    "Place",
    "get_places_api_key",
]

LOGGER = logging.getLogger("places.google")

FIELD_MASK_NEARBY = "places.id,places.displayName,places.location,places.types"
FIELD_MASK_TEXT = "places.id,places.displayName,places.location,places.types"
DEFAULT_INCLUDED_TYPES: Sequence[str] = (
    "train_station",
    "subway_station",
    "bus_station",
)
VALID_TYPES: Set[str] = set(DEFAULT_INCLUDED_TYPES)
_API_BASE = "https://places.googleapis.com/v1"


class GooglePlacesError(RuntimeError):
    """Raised for unrecoverable errors when talking to Google Places."""


class GooglePlacesTileError(GooglePlacesError):
    """Raised when a particular tile cannot be processed."""


class GooglePlacesPermissionError(GooglePlacesError):
    """Raised when the Places API rejects the request due to missing access."""


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
    max_result_count: int = 20


class GooglePlacesClient:
    """Lightweight API client with retry and pagination support."""

    def __init__(
        self,
        config: GooglePlacesConfig,
        *,
        session: Optional[requests.Session] = None,
        quota: Optional[MonthlyQuota] = None,
        quota_config: Optional[QuotaConfig] = None,
        quota_state_path: Optional[Path] = None,
        enforce_quota: bool = False,
    ) -> None:
        self._config = config
        self._session = session or requests.Session()
        self.request_count = 0
        self._quota = quota
        self._quota_config = quota_config
        self._quota_state_path = quota_state_path
        self._enforce_quota = enforce_quota
        self._quota_skipped_kinds: Set[str] = set()
        self._included_types = self._sanitize_included_types(config.included_types)
        self._radius_m = self._clamp_radius(config.radius_m)
        self._max_result_count = self._clamp_max_result_count(config.max_result_count)

    def iter_nearby(self, tiles: Iterable[Tile]) -> Iterator[Place]:
        for tile in tiles:
            if self._quota_skipped_kinds and self._quota_active:
                LOGGER.info("Skipping remaining tiles due to quota exhaustion")
                break
            yield from self._iter_tile(tile)
            if self._quota_skipped_kinds and self._quota_active:
                break

    def _iter_tile(self, tile: Tile) -> Iterator[Place]:
        base_body: Dict[str, object] = {
            "languageCode": self._config.language,
            "includedTypes": self._included_types,
            "locationRestriction": {
                "circle": {
                    "center": {
                        "latitude": tile.latitude,
                        "longitude": tile.longitude,
                    },
                    "radius": self._radius_m,
                }
            },
        }
        if self._config.region:
            base_body["regionCode"] = self._config.region
        if self._max_result_count:
            base_body["maxResultCount"] = self._max_result_count

        page_token: Optional[str] = None
        while True:
            body = dict(base_body)
            if page_token:
                body["pageToken"] = page_token
            try:
                response = self._post(
                    "places:searchNearby",
                    body,
                    quota_kind="nearby",
                    field_mask=FIELD_MASK_NEARBY,
                )
            except GooglePlacesError:
                raise
            except Exception as exc:  # pragma: no cover - defensive
                raise GooglePlacesTileError(
                    f"Unexpected error while fetching tile {tile!r}: {exc}"
                ) from exc

            places = response.get("places", [])
            if response.get("skipped_due_to_quota"):
                break
            if not isinstance(places, list):
                raise GooglePlacesTileError(
                    f"Unexpected payload for tile {tile!r}: {response!r}"
                )

            for raw in places:
                place = self._parse_place(raw)
                if place is not None:
                    yield place

            next_token = response.get("nextPageToken")
            page_token = next_token if isinstance(next_token, str) else None
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

    def _post(
        self,
        endpoint: str,
        body: Dict[str, object],
        *,
        quota_kind: Optional[str] = None,
        field_mask: str = FIELD_MASK_NEARBY,
    ) -> Dict[str, object]:
        if quota_kind and self._quota_active:
            quota = cast(MonthlyQuota, self._quota)
            cfg = cast(QuotaConfig, self._quota_config)
            reset = quota.maybe_reset_month()
            if reset:
                self._save_quota_state()
            if not quota.can_consume(quota_kind, cfg):
                if quota_kind not in self._quota_skipped_kinds:
                    LOGGER.warning(
                        "Places free cap reached for %s this month; skipping remote calls. Keeping existing cache.",
                        quota_kind,
                    )
                self._quota_skipped_kinds.add(quota_kind)
                return {"places": [], "skipped_due_to_quota": True}

        url = f"{_API_BASE}/{endpoint}"
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self._config.api_key,
            "X-Goog-FieldMask": field_mask,
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
                        payload = response.json()
                    except ValueError as exc:
                        raise GooglePlacesError(
                            "Invalid JSON payload received from Places API"
                        ) from exc
                    if quota_kind and self._quota_active:
                        self._record_successful_request(quota_kind)
                    return payload
                if response.status_code in {429, 500, 502, 503, 504}:
                    last_error = GooglePlacesError(
                        f"HTTP {response.status_code}: {response.text[:200]}"
                    )
                elif response.status_code in {401, 403}:
                    details = self._extract_error_details(response)
                    raise GooglePlacesPermissionError(details)
                else:
                    message = self._format_error_message(response)
                    raise GooglePlacesError(
                        f"Failed to fetch places ({response.status_code}): {message}"
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

    def _extract_error_details(self, response: requests.Response) -> str:
        return self._format_error_message(response)

    def _format_error_message(self, response: requests.Response) -> str:
        status_code = response.status_code
        default = f"Request failed with status {status_code}: {response.text[:200]}"
        try:
            payload = response.json()
        except ValueError:
            return default
        if not isinstance(payload, dict):
            return default

        if "error" in payload and isinstance(payload["error"], dict):
            payload = payload["error"]

        message = payload.get("message")
        status = payload.get("status")
        formatted: Optional[str] = None

        details = payload.get("details")
        if isinstance(details, list):
            for detail in details:
                if not isinstance(detail, dict):
                    continue
                detail_type = detail.get("@type")
                if not isinstance(detail_type, str) or not detail_type.endswith("BadRequest"):
                    continue
                violations = detail.get("fieldViolations")
                if not isinstance(violations, list) or not violations:
                    continue
                parts = []
                for violation in violations:
                    if not isinstance(violation, dict):
                        continue
                    field = violation.get("field")
                    description = violation.get("description")
                    fragment = ""
                    if isinstance(field, str) and field:
                        fragment = field
                    if isinstance(description, str) and description:
                        fragment = f"{fragment}: {description}" if fragment else description
                    if fragment:
                        parts.append(fragment)
                if parts:
                    base = message if isinstance(message, str) and message else f"HTTP {status_code}"
                    formatted = f"{base} | {'; '.join(parts)}"
                    break

        if not formatted and isinstance(message, str) and message:
            formatted = message

        if formatted and isinstance(status, str) and status:
            formatted = f"{status}: {formatted}"

        return formatted or default

    def _sanitize_included_types(self, raw_types: Iterable[str]) -> List[str]:
        seen: Set[str] = set()
        sanitized: List[str] = []
        for item in raw_types:
            if not isinstance(item, str):
                continue
            candidate = item.strip().lower()
            if not candidate:
                continue
            if candidate not in VALID_TYPES:
                LOGGER.warning("Ignoring unsupported place type: %s", item)
                continue
            if candidate in seen:
                continue
            sanitized.append(candidate)
            seen.add(candidate)
        if not sanitized:
            sanitized = list(DEFAULT_INCLUDED_TYPES)
        return sanitized

    def _clamp_radius(self, radius: int) -> int:
        return max(1, min(50000, radius))

    def _clamp_max_result_count(self, value: int) -> int:
        return max(1, min(20, value)) if value else 0

    def _backoff(self, attempt: int) -> float:
        base = 0.5 * (2 ** (attempt - 1))
        jitter = random.uniform(0, 0.5)
        return base + jitter

    @property
    def _quota_active(self) -> bool:
        return (
            self._enforce_quota
            and self._quota is not None
            and self._quota_config is not None
            and self._quota_state_path is not None
        )

    @property
    def quota_skipped_kinds(self) -> Set[str]:
        return set(self._quota_skipped_kinds)

    def _record_successful_request(self, kind: str) -> None:
        if not self._quota_active:
            return
        try:
            quota = cast(MonthlyQuota, self._quota)
            cfg = cast(QuotaConfig, self._quota_config)
            quota.consume(kind, cfg)
            self._save_quota_state()
        except Exception as exc:  # pragma: no cover - defensive
            raise GooglePlacesError(f"Failed to persist quota state: {exc}") from exc

    def _save_quota_state(self) -> None:
        if not self._quota_active:
            return
        try:
            quota = cast(MonthlyQuota, self._quota)
            path = cast(Path, self._quota_state_path)
            quota.save_atomic(path)
        except OSError as exc:
            LOGGER.error("Failed to save Places quota state: %s", exc)
            raise GooglePlacesError("Failed to save quota state") from exc


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
