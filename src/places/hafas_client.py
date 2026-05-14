"""Native HAFAS (ÖBB Scotty) Mgate client for coordinate fallback.

This module is the third tier of the station-directory enrichment
pipeline. After OSM Overpass and before Google Places, callers ask
:func:`enrich_station_with_hafas` for a station's coordinates by name.
A single HAFAS Mgate ``LocMatch`` request returns the canonical
location row, which is normalised into a :class:`HafasLocation`
TypedDict and returned to the caller.

Resilience is layered to mirror the OSM Overpass client:

* :func:`src.utils.http.session_with_retries` adds urllib3-level
  retries with jitter for transient errors.
* The module-level :class:`CircuitBreaker` protects the cron pipeline
  from self-DDoS when HAFAS is down: five consecutive failures cool
  the upstream for five minutes.
* :func:`src.utils.http.request_safe` enforces SSRF / DNS-rebinding /
  size / content-type guards.

The HAFAS profile (``salt`` / ``ver`` / ``aid`` / ``client``) is loaded
lazily from ``data/hafas_profile.json``. The companion
``scripts/sync_hafas_profile.py`` refreshes the file from upstream
before each cron tick. When the profile is absent (developer's local
machine, first-time clone) :func:`enrich_station_with_hafas` returns
``None`` instead of raising — the caller treats HAFAS as unavailable
and falls through to the Google Places tier.

No external HAFAS client library is used. The Mgate request payload is
constructed directly and — when the upstream profile carries a salt —
signed with an MD5 mac. ÖBB's current upstream profile carries no salt
and accepts unsigned requests.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
from pathlib import Path
from typing import Any, Final, TypedDict, cast

import requests

from ..utils.circuit_breaker import CircuitBreaker, CircuitBreakerOpen
from ..utils.files import read_capped_json
from ..utils.http import request_safe, session_with_retries
from ..utils.logging import sanitize_log_arg

__all__ = [
    "HafasLocation",
    "HafasProfile",
    "HafasProfileError",
    "enrich_station_with_hafas",
]

LOGGER = logging.getLogger("places.hafas")


class HafasLocation(TypedDict):
    """Normalised HAFAS station location returned to enrichment callers."""

    name: str
    extId: str
    lon: float
    lat: float


class HafasProfile(TypedDict):
    """In-memory view of ``data/hafas_profile.json``.

    Mirrors the on-disk JSON produced by
    ``scripts/sync_hafas_profile.py``: a flat ``salt`` / ``ver`` pair,
    plus the ``auth`` and ``client`` sub-objects that the Mgate request
    envelope carries verbatim.
    """

    salt: str
    ver: str
    auth: dict[str, str]
    client: dict[str, str]


class HafasProfileError(RuntimeError):
    """Raised when the on-disk HAFAS profile is missing or malformed.

    The public :func:`enrich_station_with_hafas` entry point catches
    this and returns ``None``; the error type is kept distinct from
    other RuntimeErrors so callers that want to surface a profile
    problem (the sync script before kicking off the enrichment) can
    distinguish it from a transient upstream failure.
    """


_HAFAS_ENDPOINT: Final[str] = "https://fahrplan.oebb.at/bin/mgate.exe"

# Per-call response cap. A LocMatch response with ``maxLoc=1`` weighs
# ~2 KiB on the wire; 256 KiB is two orders of magnitude above
# production state and well below ``MAX_PAYLOAD_SIZE`` (10 MiB).
_MAX_RESPONSE_BYTES: Final[int] = 256 * 1024

# Slowloris-defence ceiling for the whole request (connect + read).
# A LocMatch round-trip completes in < 1 s under normal conditions;
# 20 s absorbs a degraded upstream without stalling the cron tick.
_REQUEST_TIMEOUT_S: Final[float] = 20.0

# Coordinate scale used by HAFAS. The wire format carries lon/lat as
# integers multiplied by 1e6 (e.g. ``x=16377778`` for longitude
# 16.377778°). We normalise to floats at parse time so callers always
# see WGS84 decimals.
_HAFAS_COORD_SCALE: Final[float] = 1_000_000.0

_USER_AGENT: Final[str] = (
    "wien-oepnv-hafas/1.0 "
    "(+https://github.com/Origamihase/wien-oepnv; cron-pipeline)"
)

_PROFILE_PATH: Final[Path] = (
    Path(__file__).resolve().parents[2] / "data" / "hafas_profile.json"
)

_BREAKER: Final[CircuitBreaker] = CircuitBreaker(
    "hafas_enrichment",
    failure_threshold=5,
    recovery_timeout=300.0,
)

_PROFILE_LOCK: Final[threading.Lock] = threading.Lock()


class _ProfileState:
    """Module-level cache for the lazily-loaded HAFAS profile.

    The state lives on a class instead of bare module globals so
    CodeQL's ``py/unused-global-variable`` analysis (which treats
    ``global``-statement reads/writes inside a single function as
    self-use and flags the variable as unused) sees clear
    attribute-level reads and writes across the public entry point.
    The ``cache`` slot holds the parsed profile after a successful
    first load; the ``load_failed`` slot pins the "do not retry in
    this process" decision so the cron log isn't spammed with the
    same diagnostic on every subsequent station lookup.
    """

    cache: HafasProfile | None = None
    load_failed: bool = False


def _load_profile(path: Path = _PROFILE_PATH) -> HafasProfile:
    """Validate-and-narrow the on-disk profile JSON.

    Raises :class:`HafasProfileError` if the file is missing, the
    payload is not a JSON object, or any of the required fields is
    missing / mistyped. The public entry point catches this and
    short-circuits to ``None`` so the cron pipeline can fall through
    to Google Places.
    """
    payload = read_capped_json(
        path,
        label="HAFAS profile",
        logger=LOGGER,
    )
    if payload is None:
        raise HafasProfileError(
            f"HAFAS profile not found or unreadable at {path.name}"
        )
    if not isinstance(payload, dict):
        raise HafasProfileError("HAFAS profile JSON must be an object")
    payload_dict: dict[str, Any] = cast(dict[str, Any], payload)

    salt_value = payload_dict.get("salt")
    ver_value = payload_dict.get("ver")
    auth_value = payload_dict.get("auth")
    client_value = payload_dict.get("client")

    if not isinstance(salt_value, str):
        raise HafasProfileError("HAFAS profile is missing string field 'salt'")
    if not isinstance(ver_value, str) or not ver_value:
        raise HafasProfileError("HAFAS profile is missing string field 'ver'")
    if not isinstance(auth_value, dict):
        raise HafasProfileError("HAFAS profile is missing object field 'auth'")
    if not isinstance(client_value, dict):
        raise HafasProfileError("HAFAS profile is missing object field 'client'")

    auth: dict[str, str] = {}
    for key, value in auth_value.items():
        if isinstance(key, str) and isinstance(value, str):
            auth[key] = value
    client: dict[str, str] = {}
    for key, value in client_value.items():
        if isinstance(key, str) and isinstance(value, str):
            client[key] = value

    if auth.get("type") != "AID" or not auth.get("aid"):
        raise HafasProfileError(
            "HAFAS profile 'auth' block is missing AID/aid fields"
        )
    if not client.get("id") or not client.get("type"):
        raise HafasProfileError(
            "HAFAS profile 'client' block is missing id/type fields"
        )

    return HafasProfile(
        salt=salt_value,
        ver=ver_value,
        auth=auth,
        client=client,
    )


def _get_profile() -> HafasProfile | None:
    """Return the cached profile, loading it lazily on first call.

    Returns ``None`` once a load attempt has failed so subsequent calls
    in the same process short-circuit silently. The cache is thread-
    safe so a multi-threaded enrichment pass cannot race the load.
    """
    with _PROFILE_LOCK:
        if _ProfileState.cache is not None:
            return _ProfileState.cache
        if _ProfileState.load_failed:
            return None
        try:
            _ProfileState.cache = _load_profile()
        except HafasProfileError as exc:
            _ProfileState.load_failed = True
            LOGGER.warning(
                "HAFAS enrichment disabled: %s",
                sanitize_log_arg(str(exc)),
            )
            return None
        return _ProfileState.cache


def _build_loc_match_payload(profile: HafasProfile, station_name: str) -> dict[str, object]:
    """Build the Mgate ``LocMatch`` request envelope for *station_name*.

    The structure follows the canonical HAFAS Mgate contract used by
    the public-transport/hafas-client community profile: a single
    ``svcReqL`` entry asking for the top match of a station-typed
    query. ``maxLoc=1`` keeps the response tiny — we only ever need
    the first match for coordinate enrichment.
    """
    return {
        "id": profile["client"].get("id", "OEBB"),
        "ver": profile["ver"],
        "lang": "de",
        "auth": {
            "type": profile["auth"].get("type", "AID"),
            "aid": profile["auth"].get("aid", ""),
        },
        "client": dict(profile["client"]),
        "formatted": False,
        "svcReqL": [
            {
                "meth": "LocMatch",
                "req": {
                    "input": {
                        "field": "S",
                        "loc": {"name": station_name, "type": "S"},
                        "maxLoc": 1,
                    },
                },
            },
        ],
    }


def _serialise_payload(payload: dict[str, object]) -> str:
    """Render *payload* in HAFAS's canonical separator-free JSON form.

    The mac signing protocol hashes the exact bytes that travel on the
    wire, so the request body and the MD5 input must use the same
    separators. ``json.dumps(payload, separators=(',', ':'))``
    produces the same shape upstream hafas-client libraries use.
    """
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def _compute_mac(body: str, salt: str) -> str:
    """Return the HAFAS Mgate ``mac`` query parameter or empty string.

    HAFAS deployments without a salt accept unsigned requests; in that
    case we return ``""`` and the caller omits the ``mac`` parameter
    rather than sending an empty signature (which the upstream would
    reject as malformed).

    The hash itself is MD5 — non-cryptographic by design (HAFAS only
    uses it as a tamper check, not for authentication). The
    ``usedforsecurity=False`` keyword tells CodeQL / Bandit this is an
    intentional non-security use and silences ``S324`` /
    ``py/weak-cryptographic-algorithm``.
    """
    if not salt:
        return ""
    hasher = hashlib.md5(usedforsecurity=False)
    hasher.update(body.encode("utf-8"))
    hasher.update(salt.encode("utf-8"))
    return hasher.hexdigest()


def _build_request_url(mac: str) -> str:
    """Return the endpoint URL with an optional ``?mac=...`` query."""
    if not mac:
        return _HAFAS_ENDPOINT
    return f"{_HAFAS_ENDPOINT}?mac={mac}"


def _is_valid_wgs84_coord(lat: float, lon: float) -> bool:
    """Return True only for finite WGS84-range latitude/longitude pairs.

    Rejects ``NaN`` / ``+Inf`` / ``-Inf`` (which ``json.loads`` accepts
    in Python's default lenient mode from non-standard ``NaN`` /
    ``Infinity`` literals) and any value outside the geodetic valid
    ranges ``lat`` ∈ ``[-90, 90]``, ``lon`` ∈ ``[-180, 180]``. A
    compromised HAFAS upstream (or MITM) that smuggles non-finite or
    nonsensical coordinates is rejected at the parser boundary before
    the value can land in ``data/stations.json`` — invalid per RFC
    8259 and broken in every strict downstream consumer.
    """
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return False
    return -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0


def _extract_first_location(payload: object) -> HafasLocation | None:
    """Walk the HAFAS response and return the first usable location.

    The response shape is::

        {
          "svcResL": [
            {
              "meth": "LocMatch",
              "err": "OK",
              "res": {"match": {"locL": [{"name": …, "extId": …,
                                          "crd": {"x": …, "y": …}}]}},
            }
          ]
        }

    Any deviation — missing key, wrong type, empty list, non-OK
    service-level error code — returns ``None`` so callers can treat
    HAFAS as having no match. The function is intentionally defensive:
    a single upstream payload drift is a 0-cost soft failure, not a
    pipeline crash.
    """
    if not isinstance(payload, dict):
        return None
    svc_res_list = payload.get("svcResL")
    if not isinstance(svc_res_list, list) or not svc_res_list:
        return None
    first_service = svc_res_list[0]
    if not isinstance(first_service, dict):
        return None
    if first_service.get("err") not in (None, "OK"):
        return None
    res = first_service.get("res")
    if not isinstance(res, dict):
        return None
    match = res.get("match")
    if not isinstance(match, dict):
        return None
    locations = match.get("locL")
    if not isinstance(locations, list) or not locations:
        return None
    first_location = locations[0]
    if not isinstance(first_location, dict):
        return None

    name = first_location.get("name")
    ext_id = first_location.get("extId")
    coords = first_location.get("crd")
    if not isinstance(name, str) or not name.strip():
        return None
    if not isinstance(ext_id, str) or not ext_id.strip():
        return None
    if not isinstance(coords, dict):
        return None

    x_raw = coords.get("x")
    y_raw = coords.get("y")
    if not isinstance(x_raw, int | float) or isinstance(x_raw, bool):
        return None
    if not isinstance(y_raw, int | float) or isinstance(y_raw, bool):
        return None

    lon = float(x_raw) / _HAFAS_COORD_SCALE
    lat = float(y_raw) / _HAFAS_COORD_SCALE
    if not _is_valid_wgs84_coord(lat, lon):
        return None
    return HafasLocation(name=name, extId=ext_id, lon=lon, lat=lat)


def _fetch_hafas_location(station_name: str) -> HafasLocation | None:
    """Issue a single Mgate ``LocMatch`` request and parse the response.

    Returns ``None`` when the upstream replied with no match. Raises
    :class:`requests.RequestException` /
    :class:`~src.places.hafas_client.HafasProfileError` on
    infrastructure-level failures so the surrounding
    :class:`CircuitBreaker` records the failure.
    """
    profile = _get_profile()
    if profile is None:
        raise HafasProfileError("HAFAS profile not loaded")

    payload = _build_loc_match_payload(profile, station_name)
    body = _serialise_payload(payload)
    mac = _compute_mac(body, profile["salt"])
    url = _build_request_url(mac)

    session = session_with_retries(
        user_agent=_USER_AGENT,
        timeout=(min(5.0, _REQUEST_TIMEOUT_S), _REQUEST_TIMEOUT_S),
        allowed_methods=("GET", "POST"),
    )
    try:
        response = request_safe(
            session,
            url,
            method="POST",
            max_bytes=_MAX_RESPONSE_BYTES,
            timeout=_REQUEST_TIMEOUT_S,
            allowed_content_types=("application/json",),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json;charset=UTF-8",
                "User-Agent": _USER_AGENT,
            },
            data=body.encode("utf-8"),
        )
    finally:
        session.close()

    try:
        decoded = response.json()
    except (ValueError, RecursionError) as exc:
        # ``RecursionError`` defends against a JSON depth-bomb planted
        # in a compromised / MITM'd HAFAS response. Treat both shapes
        # as soft failures — the breaker counts the call as a failure
        # via the outer RequestException-shaped path used by callers
        # that want resilient behaviour.
        LOGGER.warning(
            "HAFAS returned non-JSON / depth-bomb payload for station: %s",
            sanitize_log_arg(station_name),
        )
        raise requests.RequestException("HAFAS returned invalid JSON payload") from exc

    return _extract_first_location(decoded)


def enrich_station_with_hafas(station_name: str) -> HafasLocation | None:
    """Return coordinates for *station_name* via HAFAS, or ``None``.

    The call routes through the module-level :class:`CircuitBreaker`
    so a recurring upstream failure short-circuits subsequent calls
    for five minutes. ``CircuitBreakerOpen`` is caught and converted
    to ``None`` so callers can simply branch on
    ``coords is None``. Profile-loading failures and infrastructure
    errors (network, SSRF rejection, malformed JSON) likewise resolve
    to ``None`` after a log line — HAFAS is a best-effort tier whose
    failures must never crash the cron pipeline.
    """
    trimmed = station_name.strip()
    if not trimmed:
        return None

    try:
        return _BREAKER.call(_fetch_hafas_location, trimmed)
    except CircuitBreakerOpen:
        LOGGER.info(
            "HAFAS enrichment skipped (breaker open) for station: %s",
            sanitize_log_arg(trimmed),
        )
        return None
    except HafasProfileError as exc:
        # The profile loader already logged the diagnostic the first
        # time it failed; this catch is defensive against a later
        # in-process eviction of the cached profile.
        LOGGER.debug(
            "HAFAS enrichment unavailable for station %s: %s",
            sanitize_log_arg(trimmed),
            sanitize_log_arg(str(exc)),
        )
        return None
    except requests.RequestException as exc:
        LOGGER.warning(
            "HAFAS enrichment failed for station %s: %s",
            sanitize_log_arg(trimmed),
            sanitize_log_arg(type(exc).__name__),
        )
        return None
    except ValueError as exc:
        # ``request_safe`` raises ``ValueError`` for SSRF / size /
        # content-type failures. Surface as a warning so operators can
        # spot a misconfigured endpoint without crashing the cron.
        LOGGER.warning(
            "HAFAS enrichment rejected by request_safe for station %s: %s",
            sanitize_log_arg(trimmed),
            sanitize_log_arg(type(exc).__name__),
        )
        return None
