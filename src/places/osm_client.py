"""Client abstraction for the OpenStreetMap Overpass API.

OSM is now the *primary* directory enrichment source for the project. The
Overpass API is queried for nodes, ways and relations tagged with
``public_transport=station``, ``public_transport=stop_area``,
``railway=station`` or ``railway=halt`` strictly inside the Vienna
bounding box. A best-effort match against the existing station list is
produced via ``convert_to_place`` so that the existing
``src/places/merge.py`` pipeline can integrate the OSM result without
any further glue code.

Resilience is layered:

* ``session_with_retries`` adds urllib3-level retries with jitter for
  transient errors (429, 5xx, connection resets).
* The module-level :class:`CircuitBreaker` protects the cron pipeline
  from self-DDoS when the public Overpass instance is down. It opens
  after five consecutive failures and stays open for five minutes — the
  Overpass operator's recommended cool-down for free-tier consumers.
* ``request_safe`` enforces SSRF, redirect and content-type guards.

A mandatory descriptive ``User-Agent`` is set on every request — the
Overpass operator's fair-use policy explicitly requires this.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, NotRequired, TypedDict, cast
from collections.abc import Iterable, Iterator
from urllib.parse import urlparse

import requests

from ..utils.circuit_breaker import CircuitBreaker, CircuitBreakerOpen
from ..utils.http import request_safe, session_with_retries
from ..utils.logging import sanitize_log_arg

from .client import Place
from .merge import BoundingBox

__all__ = [
    "DEFAULT_OVERPASS_ENDPOINTS",
    "OSMOverpassClient",
    "OSMOverpassConfig",
    "OSMOverpassError",
    "OSMStation",
    "OSMTags",
    "VIENNA_BOUNDING_BOX",
    "build_overpass_query",
    "convert_to_place",
    "get_overpass_endpoint",
]


# Strictly-typed view of the Overpass tag bag we consume. Overpass
# elements carry an open tag dictionary so any key is technically valid;
# this TypedDict pins the keys the project actually inspects (name
# selection, type derivation, accessibility flags, operator attribution)
# so mypy --strict catches typos at read sites. Several OSM tag keys are
# NOT valid Python identifiers (``name:de``, ``ref:IFOPT`` …), so the
# functional ``TypedDict(...)`` form is used; every field is wrapped in
# ``NotRequired`` because Overpass payloads almost never carry the full
# inventory at once and we never want a missing key to be a type error.
# Unknown keys are tolerated at parse time (see ``_normalize_tags``) but
# discouraged at consumption sites; the canonical source of truth for
# the OSM enrichment pipeline is this TypedDict.
OSMTags = TypedDict(
    "OSMTags",
    {
        # --- Naming hierarchy (consumed by ``_select_name``)
        # Listed in the priority order the selector applies.
        "name": NotRequired[str],
        "name:de": NotRequired[str],
        "short_name": NotRequired[str],
        "short_name:de": NotRequired[str],
        "alt_name": NotRequired[str],
        "alt_name:de": NotRequired[str],
        "official_name": NotRequired[str],
        "official_name:de": NotRequired[str],
        "loc_name": NotRequired[str],
        "loc_name:de": NotRequired[str],
        # --- Public-transport / railway classification
        "public_transport": NotRequired[str],
        "railway": NotRequired[str],
        "train": NotRequired[str],
        "subway": NotRequired[str],
        "light_rail": NotRequired[str],
        "tram": NotRequired[str],
        "bus": NotRequired[str],
        "station": NotRequired[str],
        # --- Accessibility / operator metadata (informational only)
        "wheelchair": NotRequired[str],
        "operator": NotRequired[str],
        "network": NotRequired[str],
        "ref": NotRequired[str],
        "ref:IFOPT": NotRequired[str],
        "uic_ref": NotRequired[str],
    },
)

LOGGER = logging.getLogger("places.osm")

# Trusted Overpass mirrors. The free public endpoint is the canonical
# fallback; the de-mirror endpoint is offered to operators who can
# tolerate slightly more aggressive rate-limits but want lower latency
# from EU. ``OVERPASS_URL`` env-overrides are validated against this
# allow-list to keep an attacker-controlled host from hijacking the
# station directory on the cron pipeline.
DEFAULT_OVERPASS_ENDPOINTS: tuple[str, ...] = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)

_TRUSTED_OVERPASS_HOSTS: frozenset[str] = frozenset(urlparse(url).hostname or "" for url in DEFAULT_OVERPASS_ENDPOINTS)


# Vienna bounding box (south, west, north, east). Pulled from the
# WGS84 envelope of ``LANDESGRENZEOGD.json`` rounded outward by ~250 m
# so polygon-edge stations are not clipped by float drift. Used both as
# the Overpass ``bbox`` filter and as the fall-back ``BoundingBox`` for
# ``merge_places`` callers that don't already have one.
VIENNA_BOUNDING_BOX: BoundingBox = BoundingBox(min_lat=48.1180, min_lng=16.1820, max_lat=48.3230, max_lng=16.5780)

# Overpass query timeout (seconds). Sent as ``[timeout:N]`` inside the
# QL header so the upstream aborts if the query plan is too expensive.
# Mirrors ``MAX_OEBB_FETCH_TIMEOUT`` (25s) and is well below the
# Overpass operator's free-tier 180s default cap.
_OVERPASS_QUERY_TIMEOUT_S = 25

# Slowloris-defence ceiling for the whole request (connect + read).
_MAX_TIMEOUT_S = 30.0

# Per-call response cap. Vienna's full Overpass JSON for the four
# tag-pairs sits at ~600 KiB; 5 MiB is ~8x production state and well
# below ``MAX_PAYLOAD_SIZE`` (10 MiB).
_MAX_RESPONSE_BYTES = 5 * 1024 * 1024

# Module-level circuit breaker. Five consecutive failures cool off the
# upstream for five minutes — matches the rate-limit budget the
# Overpass operator publishes for free-tier consumers.
_BREAKER = CircuitBreaker(
    "places.osm.overpass",
    failure_threshold=5,
    recovery_timeout=300.0,
)


class OSMOverpassError(RuntimeError):
    """Raised when the Overpass API returns an unrecoverable error."""


@dataclass(frozen=True)
class OSMStation:
    """Normalised station entry materialised from a single Overpass element."""

    osm_id: str
    osm_type: str
    name: str
    latitude: float
    longitude: float
    tags: OSMTags

    @property
    def types(self) -> list[str]:
        """Stable, ordered list of recognised public_transport / railway tags.

        We surface these alongside the name so the merge pipeline can
        record the type information in ``stations.json`` without keeping
        a reference to the full raw tag bag.
        """
        ordered: list[str] = []
        seen: set[str] = set()
        for key in ("public_transport", "railway"):
            value = self.tags.get(key)
            if isinstance(value, str) and value and value not in seen:
                seen.add(value)
                ordered.append(value)
        return ordered


@dataclass(frozen=True)
class OSMOverpassConfig:
    """Frozen config for an :class:`OSMOverpassClient` instance.

    ``endpoint`` is validated against :data:`_TRUSTED_OVERPASS_HOSTS`
    inside ``__post_init__`` so an env override pointing at an attacker
    host is rejected before any network request is made. ``timeout_s``
    is clamped to :data:`_MAX_TIMEOUT_S` and ``user_agent`` is required
    to be a descriptive string per the Overpass fair-use policy.
    """

    endpoint: str
    user_agent: str
    bounding_box: BoundingBox = VIENNA_BOUNDING_BOX
    timeout_s: float = 25.0
    query_timeout_s: int = _OVERPASS_QUERY_TIMEOUT_S
    max_response_bytes: int = _MAX_RESPONSE_BYTES

    def __post_init__(self) -> None:
        host = (urlparse(self.endpoint).hostname or "").lower()
        if host not in _TRUSTED_OVERPASS_HOSTS:
            raise ValueError(f"Overpass endpoint {self.endpoint!r} is not on the trusted host allow-list {sorted(_TRUSTED_OVERPASS_HOSTS)}")
        if not self.user_agent or not self.user_agent.strip():
            raise ValueError("OSMOverpassConfig.user_agent is required")
        if self.timeout_s <= 0 or self.query_timeout_s <= 0:
            raise ValueError("Overpass timeouts must be positive")
        if self.timeout_s > _MAX_TIMEOUT_S:
            object.__setattr__(self, "timeout_s", _MAX_TIMEOUT_S)
        if self.max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")


def get_overpass_endpoint() -> str:
    """Return the trusted Overpass endpoint URL.

    ``OVERPASS_URL`` may be set to one of :data:`DEFAULT_OVERPASS_ENDPOINTS`
    to override the default; any other value is rejected with a warning
    and the built-in default is used instead. Because the env override
    is rendered into the cron pipeline's outbound HTTPS request, the
    allow-list keeps an attacker (compromised secret store / leaked CI
    env) from redirecting the station enrichment fetch.
    """
    raw = os.getenv("OVERPASS_URL", "").strip()
    if not raw:
        return DEFAULT_OVERPASS_ENDPOINTS[0]
    if raw in DEFAULT_OVERPASS_ENDPOINTS:
        return raw
    LOGGER.warning(
        "OVERPASS_URL %s is not on the trusted Overpass host allow-list; falling back to default endpoint",
        sanitize_log_arg(raw),
    )
    return DEFAULT_OVERPASS_ENDPOINTS[0]


def build_overpass_query(bounding_box: BoundingBox, query_timeout_s: int) -> str:
    """Render the Overpass QL query covering all required station tags.

    The selection covers public_transport=station/stop_area and
    railway=station/halt across nodes, ways and relations. ``out center
    tags;`` retrieves the centre coordinates for ways/relations so we
    can treat them uniformly with nodes downstream.
    """
    if query_timeout_s <= 0:
        raise ValueError("query_timeout_s must be positive")
    bbox = f"{bounding_box.min_lat:.6f},{bounding_box.min_lng:.6f},{bounding_box.max_lat:.6f},{bounding_box.max_lng:.6f}"
    body_lines: list[str] = []
    for tag_pair in (
        ("public_transport", "station"),
        ("public_transport", "stop_area"),
        ("railway", "station"),
        ("railway", "halt"),
    ):
        key, value = tag_pair
        for kind in ("node", "way", "relation"):
            body_lines.append(f'  {kind}["{key}"="{value}"]({bbox});')
    body = "\n".join(body_lines)
    return f"[out:json][timeout:{query_timeout_s}];\n(\n{body}\n);\nout center tags;\n"


class OSMOverpassClient:
    """Lightweight wrapper around the Overpass API.

    The class is shaped to feed
    ``src/places/merge.py:merge_places``: ``fetch_stations`` returns
    :class:`OSMStation` and ``convert_to_place`` lifts the result into
    :class:`Place` instances accepted by the existing merger.
    """

    def __init__(
        self,
        config: OSMOverpassConfig,
        *,
        session: requests.Session | None = None,
    ) -> None:
        self._config = config
        self._session = session or session_with_retries(
            user_agent=config.user_agent,
            timeout=(min(5.0, config.timeout_s), config.timeout_s),
            allowed_methods=("GET", "POST"),
        )

    def __enter__(self) -> OSMOverpassClient:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.close()

    def close(self) -> None:
        if self._session is not None:
            try:
                self._session.close()
            except Exception as exc:  # pragma: no cover - defensive
                # Security (Clear-Text-Logging Drift Round 3): defensive
                # framework catch-all — sanitize the exception text so any
                # control characters / ANSI escapes in the underlying
                # session's error message cannot forge log lines.
                LOGGER.debug(
                    "Error closing OSM session: %s",
                    sanitize_log_arg(str(exc)),
                )

    def fetch_stations(self) -> list[OSMStation]:
        """Query Overpass for Vienna stations with the project's tag set.

        The call is wrapped in :class:`CircuitBreaker` so a recurring
        failure cools off the upstream for five minutes instead of
        retrying on every cron tick. ``CircuitBreakerOpen``,
        :class:`requests.RequestException` and any deserialisation
        errors are converted to :class:`OSMOverpassError` — callers
        catch the latter to fall back to Google Places.
        """
        try:
            payload = _BREAKER.call(self._fetch_payload)
        except CircuitBreakerOpen as exc:
            raise OSMOverpassError("OSM Overpass breaker is open; refusing call") from exc
        return list(_iter_stations(payload, self._config.bounding_box))

    def _fetch_payload(self) -> dict[str, Any]:
        query = build_overpass_query(self._config.bounding_box, self._config.query_timeout_s)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": self._config.user_agent,
        }
        try:
            response = request_safe(
                self._session,
                self._config.endpoint,
                method="POST",
                max_bytes=self._config.max_response_bytes,
                timeout=self._config.timeout_s,
                allowed_content_types=("application/json", "application/osm3s+xml"),
                headers=headers,
                data={"data": query},
            )
        except requests.RequestException as exc:
            raise OSMOverpassError(f"Overpass request failed: {type(exc).__name__}") from exc
        except ValueError as exc:
            # Security (Clear-Text-Logging Drift Round 3): ``request_safe``
            # raises ``ValueError`` for SSRF / size / content-type failures
            # with a sanitised URL embedded in the message. Embedding the
            # full ``str(exc)`` here propagates that text upstream via
            # ``str(OSMOverpassError)`` to ``update_station_directory.py``'s
            # framework catch-all, where it is logged. Today's
            # ``request_safe`` ValueErrors do not carry credentials, but
            # defense-in-depth says we surface only the type name so a
            # future ValueError shape (with credential-bearing text) cannot
            # silently re-enable a leak. The chained ``from exc`` keeps
            # the full context available for ``logging.exception``-style
            # tracebacks where operators have explicitly opted in.
            raise OSMOverpassError(f"Overpass request rejected: {type(exc).__name__}") from exc

        if response.status_code != 200:
            raise OSMOverpassError(f"Overpass returned HTTP {response.status_code}")

        try:
            payload = response.json()
        except (
            ValueError,
            json.JSONDecodeError,
            requests.exceptions.JSONDecodeError,
            RecursionError,
        ) as exc:
            raise OSMOverpassError("Overpass returned invalid JSON payload") from exc

        if not isinstance(payload, dict):
            raise OSMOverpassError(f"Overpass returned unexpected JSON type: {type(payload).__name__}")

        return cast(dict[str, Any], payload)


def _coerce_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _coerce_str(value: object) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def _extract_coordinates(element: dict[str, Any]) -> tuple[float, float] | None:
    """Return (lat, lon) for a node, way or relation Overpass element."""
    lat = _coerce_float(element.get("lat"))
    lon = _coerce_float(element.get("lon"))
    if lat is not None and lon is not None:
        return lat, lon
    centre = element.get("center")
    if isinstance(centre, dict):
        lat = _coerce_float(centre.get("lat"))
        lon = _coerce_float(centre.get("lon"))
        if lat is not None and lon is not None:
            return lat, lon
    return None


_NAME_PRIORITY: tuple[str, ...] = (
    # Passenger-friendly German labels rank highest. They carry the
    # diacritics and full station-name spelling Wiener Linien / OSM
    # editors actually maintain (e.g. "Wien Hauptbahnhof"), and they
    # consistently override cryptic ÖBB internal names like
    # "Wien Hbf" / "Wien Hbf (Tief)".
    "name:de",
    "name",
    # Localised long-form variants come next. They preserve compound
    # structures ("Hauptbahnhof", "Westbahnhof") that the merger leans
    # on for fuzzy alignment with the directory.
    "official_name:de",
    "official_name",
    "loc_name:de",
    "loc_name",
    # ``alt_name`` is often a passenger-friendly alias that
    # supplements the canonical ÖBB code; ranked above short_name so
    # full names beat abbreviations whenever the editor has supplied
    # both.
    "alt_name:de",
    "alt_name",
    # ``short_name`` is a deliberate abbreviation. Only used when no
    # full-form name is available — preserves the "Hauptbahnhof" rule
    # because ``name`` / ``official_name`` will have been picked first
    # if they exist.
    "short_name:de",
    "short_name",
)


def _normalize_tags(raw: object) -> OSMTags:
    """Validate-and-narrow an Overpass element's ``tags`` mapping.

    Overpass guarantees keys and values are strings, but the JSON
    decoder returns ``object`` so the loop discards any drift defensively
    (e.g. a ``null`` value smuggled past a Vespian mirror). The result
    is cast to :data:`OSMTags` so downstream call sites benefit from
    strict typing without paying a per-key validation cost; the parser
    contract is that the returned dict is the project's canonical view
    of the element's tags, even if it carries keys not enumerated in
    the TypedDict.
    """
    if not isinstance(raw, dict):
        return cast(OSMTags, {})
    out: dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, str):
            out[key] = value
    return cast(OSMTags, out)


def _select_name(tags: OSMTags) -> str | None:
    """Return the most passenger-friendly station label available.

    The hierarchy intentionally promotes Wiener-Linien / OSM editor
    names ahead of cryptic ÖBB internal abbreviations, while still
    preserving compound forms like "Wien Hauptbahnhof" — those land in
    ``name`` / ``official_name`` first, so the long form wins by
    construction. Localised ``:de`` keys edge out the bare key when
    both exist so umlauts and full spellings (e.g. "Wien
    Praterstern" → "Wien Praterstern", not "Praterstern") are
    preserved verbatim. ``short_name`` is consulted last so a
    passenger-friendly alias (``alt_name``) beats an editor-supplied
    abbreviation whenever the OSM record carries both.
    """
    for key in _NAME_PRIORITY:
        candidate = _coerce_str(tags.get(key))
        if candidate is not None:
            return candidate
    return None


def _iter_stations(payload: dict[str, Any], bounding_box: BoundingBox) -> Iterator[OSMStation]:
    elements = payload.get("elements")
    if not isinstance(elements, list):
        return
    seen_ids: set[str] = set()
    for element in elements:
        if not isinstance(element, dict):
            continue
        osm_type = _coerce_str(element.get("type"))
        if osm_type not in {"node", "way", "relation"}:
            continue
        osm_id_raw = element.get("id")
        if not isinstance(osm_id_raw, int):
            continue
        coords = _extract_coordinates(element)
        if coords is None:
            continue
        latitude, longitude = coords
        if not bounding_box.contains(latitude, longitude):
            continue
        tags = _normalize_tags(element.get("tags"))
        name = _select_name(tags)
        if name is None:
            continue
        # Require at least one of the four tag/value pairs the query
        # filtered on. Defends against an upstream that returns extra
        # elements (the public Overpass instance is shared between many
        # users; a broken query plan can leak unrelated rows).
        if not (tags.get("public_transport") in {"station", "stop_area"} or tags.get("railway") in {"station", "halt"}):
            continue
        composite_id = f"{osm_type}/{osm_id_raw}"
        if composite_id in seen_ids:
            continue
        seen_ids.add(composite_id)
        yield OSMStation(
            osm_id=str(osm_id_raw),
            osm_type=osm_type,
            name=name,
            latitude=latitude,
            longitude=longitude,
            tags=tags,
        )


def convert_to_place(station: OSMStation) -> Place:
    """Lift an :class:`OSMStation` into the canonical :class:`Place` shape.

    The merge pipeline (:func:`src.places.merge.merge_places`) keys on
    ``Place.place_id`` and treats ``Place.types`` as the authoritative
    list of place categories. Embedding the OSM type into the
    place_id (``osm:way/12345``) keeps the identifier stable across
    runs and avoids collisions with the Google Places ``ChIJ…`` shape.
    """
    return Place(
        place_id=f"osm:{station.osm_type}/{station.osm_id}",
        name=station.name,
        latitude=station.latitude,
        longitude=station.longitude,
        types=station.types,
        formatted_address=None,
    )


def fetch_osm_stations(
    config: OSMOverpassConfig | None = None,
    *,
    user_agent: str | None = None,
) -> list[OSMStation]:
    """Convenience wrapper that builds a default config + client.

    Used by ``scripts/update_station_directory.py`` to keep the call
    site simple. Caller may pass ``user_agent`` to override the default
    descriptive string without otherwise customising the config.
    """
    if config is None:
        endpoint = get_overpass_endpoint()
        ua = user_agent or _default_user_agent()
        config = OSMOverpassConfig(endpoint=endpoint, user_agent=ua)
    with OSMOverpassClient(config) as client:
        return client.fetch_stations()


def _default_user_agent() -> str:
    """Descriptive User-Agent the Overpass operator's policy requires."""
    return "wien-oepnv-station-updater/1.0 (+https://github.com/Origamihase/wien-oepnv; cron-pipeline)"


def fetch_osm_places(
    config: OSMOverpassConfig | None = None,
    *,
    user_agent: str | None = None,
) -> list[Place]:
    """High-level helper returning :class:`Place` objects ready for merge."""
    stations = fetch_osm_stations(config, user_agent=user_agent)
    return [convert_to_place(station) for station in stations]


def filter_complete_places(places: Iterable[Place]) -> list[Place]:
    """Return only places that carry the minimum data set the merge needs.

    A "complete" OSM result has a non-empty name and finite coordinates.
    Anything else is dropped before the merger runs so the Google
    fallback is responsible only for genuinely missing entries.
    """
    out: list[Place] = []
    for place in places:
        if not place.name or not place.name.strip():
            continue
        try:
            lat = float(place.latitude)
            lon = float(place.longitude)
        except (TypeError, ValueError):
            continue
        if lat != lat or lon != lon:  # NaN check
            continue
        out.append(place)
    return out
