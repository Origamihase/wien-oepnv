"""Client abstraction for the Google Places API (New)."""

from __future__ import annotations

import logging
import os
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Set, cast

import requests

try:
    from utils.http import read_response_safe, session_with_retries, verify_response_ip
    from utils.logging import sanitize_log_arg, sanitize_log_message
except ModuleNotFoundError:
    from ..utils.http import read_response_safe, session_with_retries, verify_response_ip  # type: ignore
    from ..utils.logging import sanitize_log_arg, sanitize_log_message  # type: ignore

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

_MAX_ERROR_DETAIL = 200


def _sanitize_error_detail(detail: str, secrets: Optional[List[str]] = None) -> str:
    # Security: Mask secrets and strip control characters to avoid log injection.
    cleaned = sanitize_log_message(detail, secrets=secrets)
    return cleaned[:_MAX_ERROR_DETAIL]


def _env_int(name: str, default: int, min_v: int | None = None, max_v: int | None = None) -> int:
    raw = os.getenv(name)
    value = default
    if raw is not None:
        try:
            value = int(raw)
        except ValueError:
            LOGGER.warning("Invalid integer for %s: %s", name, sanitize_log_arg(raw))
            value = default
    if min_v is not None:
        value = max(min_v, value)
    if max_v is not None:
        value = min(max_v, value)
    return value


def _env_rank_preference(
    name: str = "PLACES_RANK_PREFERENCE", default: str = "POPULARITY"
) -> str:
    allowed = {"POPULARITY", "DISTANCE"}
    default_normalized = default.strip().upper()
    raw = os.getenv(name)
    if raw is None:
        return default_normalized
    candidate = raw.strip().upper()
    if candidate in allowed:
        return candidate
    LOGGER.warning("Invalid rank preference for %s: %s", name, sanitize_log_arg(raw))
    return default_normalized


RADIUS_M = _env_int("PLACES_RADIUS_M", 2500, 1, 50000)
MAX_RESULTS = _env_int("PLACES_MAX_RESULTS", 20, 1, 20)
RANK_PREF = _env_rank_preference()

FIELD_MASK_NEARBY = "places.id,places.displayName,places.location,places.types"
FIELD_MASK_TEXT = "places.id,places.displayName,places.location,places.types"
DEFAULT_INCLUDED_TYPES: Sequence[str] = (
    "train_station",
    "subway_station",
    "bus_station",
)
VALID_TYPES: Set[str] = set(DEFAULT_INCLUDED_TYPES)
_API_BASE = "https://places.googleapis.com/v1"
_NEARBY_CONFIG_LOGGED = False


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
        self._session = session or session_with_retries(
            user_agent="wien-oepnv-places/1.0",
            # Fallback timeout if not specified in config (though _post uses config.timeout_s)
            timeout=20,
            # Google Places API primarily uses POST, allow retries on POST
            allowed_methods=("GET", "POST"),
        )
        self.request_count = 0
        self._quota = quota
        self._quota_config = quota_config
        self._quota_state_path = quota_state_path
        self._enforce_quota = enforce_quota
        self._quota_skipped_kinds: Set[str] = set()
        self._included_types = self._sanitize_included_types(config.included_types)
        self._radius_m = RADIUS_M
        self._max_result_count = MAX_RESULTS
        self._rank_preference = RANK_PREF

    def _sanitize_arg(self, arg: object) -> object:
        return sanitize_log_arg(arg, secrets=[self._config.api_key])

    def iter_nearby(self, tiles: Iterable[Tile]) -> Iterator[Place]:
        for tile in tiles:
            if self._quota_skipped_kinds and self._quota_active:
                LOGGER.info("Skipping remaining tiles due to quota exhaustion")
                break
            yield from self._iter_tile(tile)
            if self._quota_skipped_kinds and self._quota_active:
                break

    def _iter_tile(self, tile: Tile) -> Iterator[Place]:
        global _NEARBY_CONFIG_LOGGED
        if not _NEARBY_CONFIG_LOGGED:
            LOGGER.info(
                "Nearby config: radius=%sm, max=%s, rank=%s, types=%s",
                self._radius_m,
                self._max_result_count,
                self._rank_preference,
                self._included_types,
            )
            _NEARBY_CONFIG_LOGGED = True

        base_body: Dict[str, object] = {
            "languageCode": self._config.language,
            "includedTypes": self._included_types,
            "rankPreference": self._rank_preference,
            "maxResultCount": self._max_result_count,
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
            LOGGER.warning("Ignoring unexpected place payload: %s", self._sanitize_arg(raw))
            return None
        place_id = raw.get("id")
        if not isinstance(place_id, str):
            LOGGER.warning("Skipping place without valid id: %s", self._sanitize_arg(raw))
            return None
        display_name = raw.get("displayName")
        if isinstance(display_name, dict):
            name = display_name.get("text")
        else:
            name = None
        if not isinstance(name, str):
            LOGGER.warning("Skipping place without valid name: %s", self._sanitize_arg(place_id))
            return None
        location = raw.get("location")
        if not isinstance(location, dict):
            LOGGER.warning("Skipping place without location: %s", self._sanitize_arg(place_id))
            return None
        latitude = location.get("latitude")
        longitude = location.get("longitude")
        if not isinstance(latitude, (float, int)) or not isinstance(longitude, (float, int)):
            LOGGER.warning("Skipping place with invalid coordinates: %s", self._sanitize_arg(place_id))
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
                with self._session.post(
                    url,
                    headers=headers,
                    json=body,
                    timeout=self._config.timeout_s,
                    stream=True,
                ) as response:
                    # Enforce SSRF protection (DNS rebinding check)
                    try:
                        verify_response_ip(response)
                    except ValueError as exc:
                        raise GooglePlacesError(f"Security check failed: {exc}") from exc

                    # Enforce DoS protection (limit response size)
                    try:
                        content_bytes = read_response_safe(response)
                    except ValueError as exc:
                        raise GooglePlacesError(f"Response too large: {exc}") from exc

                    # Manually populate response content so .json() and .text work as expected
                    response._content = content_bytes
                    response._content_consumed = True

                    self.request_count += 1

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
                        detail = _sanitize_error_detail(response.text, secrets=[self._config.api_key])
                        last_error = GooglePlacesError(
                            f"HTTP {response.status_code}: {detail}"
                        )
                    elif response.status_code in {401, 403}:
                        details = self._extract_error_details(response)
                        raise GooglePlacesPermissionError(details)
                    else:
                        message = self._format_error_message(response)
                        raise GooglePlacesError(
                            f"Failed to fetch places ({response.status_code}): {message}"
                        )

            except requests.RequestException as exc:
                last_error = exc
                LOGGER.warning(
                    "Request error (attempt %s/%s): %s",
                    attempt,
                    self._config.max_retries + 1,
                    self._sanitize_arg(exc),
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
        detail = _sanitize_error_detail(response.text, secrets=[self._config.api_key])
        default = f"Request failed with status {status_code}: {detail}"
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
                LOGGER.warning("Ignoring unsupported place type: %s", self._sanitize_arg(item))
                continue
            if candidate in seen:
                continue
            sanitized.append(candidate)
            seen.add(candidate)
        if not sanitized:
            sanitized = list(DEFAULT_INCLUDED_TYPES)
        return sanitized

    def _backoff(self, attempt: int) -> float:
        base = 0.5 * (2 ** (attempt - 1))
        jitter = random.uniform(0, 0.5)  # nosec B311
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
            LOGGER.error("Failed to save Places quota state: %s", self._sanitize_arg(exc))
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
