"""
VOR/VAO Provider Module.

This module implements the logic to fetch transport alerts from the VOR (Verkehrsverbund Ost-Region)
/ VAO (Verkehrsauskunft Österreich) API. It handles:
- Authentication (Access ID / Token injection)
- Station resolution (Name -> ID)
- Rate limiting and caching of request counts
- Safe HTTP fetching with retries and DoS protection
- Parsing of complex JSON responses into standardized feed items.

Configuration is primarily driven by environment variables (e.g., ``VOR_ACCESS_ID``, ``VOR_STATION_IDS``).
"""

from __future__ import annotations

import atexit
import base64
import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from collections.abc import Mapping
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from requests import RequestException, Session
from requests.auth import AuthBase

from zoneinfo import ZoneInfo

from ..utils.env import read_secret
from ..utils.files import atomic_write, read_capped_json
from ..utils.http import (
    validate_http_url,
)
from ..utils.locking import file_lock
from ..utils.logging import sanitize_log_arg, sanitize_log_message

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
DEFAULT_INFO_LINK = "https://www.vor.at/"

DEFAULT_VERSION = "v1.11.0"
DEFAULT_BASE = "https://routenplaner.verkehrsauskunft.at/vao/restproxy"
DEFAULT_BASE_URL = f"{DEFAULT_BASE}/{DEFAULT_VERSION}/"
DEFAULT_USER_AGENT = "wien-oepnv/1.0 (+https://github.com/Origamihase/wien-oepnv)"

DEFAULT_HTTP_TIMEOUT = 15
# "VAO Start" contract limit: 100 requests per day (hard limit).
DEFAULT_MAX_REQUESTS_PER_DAY = 100
# Default VOR Monitor whitelist — INTENTIONALLY EMPTY since the Stammstrecke
# migration (2026-05-09): the historical default ``"Wien Hauptbahnhof,
# Flughafen Wien"`` consumed two VOR DepartureBoard requests per cron tick
# (every hour) for stations whose disruption coverage is now provided by
# the WL / OEBB providers. After the Stammstrecke monitor was migrated from
# pyhafas to the VOR ``/trip`` endpoint (``scripts/update_stammstrecke_status.py``),
# the per-day budget became dominated by 2 trip requests × 48 cron fires =
# 96 requests/day, leaving only ~4 requests/day buffer for monthly station
# enrichment. Keeping the legacy departure-board polling on top of that
# would push the project over the contractual ``MAX_REQUESTS_PER_DAY`` cap
# (100/day). An operator who explicitly needs the legacy behaviour can
# still set ``VOR_MONITOR_STATIONS_WHITELIST`` in the environment to
# re-enable specific stations — but the project default is now "no
# departure-board polling".
RETRY_AFTER_MAX_SEC = 60.0


# Limit concurrent station fetches to avoid thread exhaustion

ZONE_VIENNA = ZoneInfo("Europe/Vienna")

VOR_USER_AGENT = os.getenv("VOR_USER_AGENT", DEFAULT_USER_AGENT)
# urllib3 retries are disabled here on purpose: every actual HTTP call to
# VOR counts against the strict 100/day quota, but quota is only
# incremented once per ``fetch_content_safe`` call (see the lock-protected
# ``save_request_count`` site below). With ``total>0`` urllib3 silently
# repeats the request on 429/5xx, so a single counted call could consume
# up to ``total+1`` real quota slots — a hard violation of the spec
# requirement that the budget must NEVER be exceeded. Application-level
# scheduling re-runs the job on the next interval, which is the correct
# place to recover from transient errors.
VOR_RETRY_OPTIONS: dict[str, Any] = {
    "total": 0,
    "backoff_factor": 0.5,
    "raise_on_status": False,
}

VOR_ACCESS_ID = ""  # nosec B105
_VOR_ACCESS_TOKEN_RAW = ""  # nosec B105
_VOR_AUTHORIZATION_HEADER = ""  # nosec B105

# Global lock for thread-safe quota management within the process
_QUOTA_LOCK = threading.RLock()
# Local cache to optimize quota checks (fail-fast)
_QUOTA_CACHE: dict[str, Any] = {"date": None, "count": 0, "unsaved_delta": 0}


def _flush_quota_cache() -> None:
    """Flush any unsaved request counts to disk on exit.

    Persists the in-memory ``unsaved_delta`` to disk WITHOUT recording a
    new request. Pre-fix this function called :func:`save_request_count`,
    which always increments ``unsaved_delta`` by ``1`` before persisting
    — every script invocation that made any VOR call therefore booked
    one phantom request beyond the actual API traffic. With 48 cron
    ticks/day and 2 real ``/trip`` calls per tick, the bug inflated the
    persisted counter from ``96`` to ``144`` requests/day, exhausting
    the contractual ``100``/day VAO Start cap after roughly 33 ticks
    and leaving the remaining ~15 hours of the day without
    Stammstrecke observations. The CSV ledger gap that bug produced
    visibly degraded the README "Letzte 60 Minuten" snapshot when one
    of the affected hours was the most recent one.
    """
    with _QUOTA_LOCK:
        _persist_quota_to_disk()

atexit.register(_flush_quota_cache)


def _get_secrets() -> list[str]:
    secrets = [s for s in [VOR_ACCESS_ID, _VOR_ACCESS_TOKEN_RAW] if s]
    return secrets


def _sanitize_message(text: str) -> str:
    """
    Sanitize log messages by masking secrets and removing control characters.
    """
    secrets = _get_secrets()
    sanitized = sanitize_log_message(text, secrets=secrets)

    # Specific handling for the auth header global:
    # never log the header value itself, even partially.
    if _VOR_AUTHORIZATION_HEADER and _VOR_AUTHORIZATION_HEADER in sanitized:
        sanitized = sanitized.replace(
            _VOR_AUTHORIZATION_HEADER, "[REDACTED_AUTH_HEADER]"
        )

    return sanitized


def _sanitize_arg(arg: Any) -> Any:
    """Helper to sanitize arguments passed to logging functions."""
    secrets = _get_secrets()
    # If the arg matches the exact header string, mask it before generic sanitization
    if (
        _VOR_AUTHORIZATION_HEADER
        and isinstance(arg, str)
        and arg == _VOR_AUTHORIZATION_HEADER
    ):
        return "[REDACTED_AUTH_HEADER]"

    return sanitize_log_arg(arg, secrets=secrets)


def _log_warning(message: str, *args: Any) -> None:
    # Ensure message is sanitized even if args are present
    sanitized_msg = _sanitize_message(message)
    if args:
        sanitized_args = tuple(_sanitize_arg(arg) for arg in args)
        log.warning(sanitized_msg, *sanitized_args)
    else:
        log.warning("%s", sanitized_msg)


def _log_error(message: str, *args: Any) -> None:
    # Ensure message is sanitized even if args are present
    sanitized_msg = _sanitize_message(message)
    if args:
        sanitized_args = tuple(_sanitize_arg(arg) for arg in args)
        log.error(sanitized_msg, *sanitized_args)
    else:
        log.error("%s", sanitized_msg)


def _get_env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _load_int_env(name: str, default: int) -> int:
    raw = _get_env(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        _log_warning(
            "Ungültiger Wert für %s: %s – verwende Standard %s", name, raw, default
        )
        return default
    if value <= 0:
        _log_warning(
            "Ungültiger Wert für %s: %s – verwende Standard %s", name, raw, default
        )
        return default
    return value


# Security: env-supplied regexes (``VOR_BUS_INCLUDE_REGEX`` /
# ``VOR_BUS_EXCLUDE_REGEX``) are compiled here and matched against every
# bus-line token during feed building. An operator typo or compromised
# config store could supply a ReDoS-vulnerable pattern (e.g. ``(a+)+$``,
# ``(.*)*``) that locks the build process at 100% CPU on certain inputs.
# We defend with two cheap, stdlib-only layers before ``re.compile``:
# (1) bound the pattern length so an oversized input cannot exhaust
# memory during compilation, and (2) heuristically reject the most
# common ReDoS construction — nested unbounded quantifiers around a
# group, i.e. ``(...+)+`` / ``(...*)*`` / ``(...?)+`` etc. The default
# bus filter patterns above contain no such constructions, so the
# fallback path stays safe. Detection is intentionally heuristic; it
# does not catch every ReDoS shape (alternation overlap such as
# ``(a|aa)+`` slips through), but it blocks the patterns that have
# historically caused real outages and falls back to the project's
# pre-vetted defaults whenever a check fires.




# Security: ``DEFAULT_HTTP_TIMEOUT`` (15s) is the Slowloris-defence ceiling for
# every VOR request — both the connect and read budget for ``fetch_content_safe``
# at lines 1173 and 1436, plus the cache-update script at
# ``scripts/update_vor_stations.py``. ``_load_int_env`` only enforces a lower
# bound (``value > 0``), so a benign-looking env override such as
# ``VOR_HTTP_TIMEOUT=99999`` (intentional misconfig, leaked CI env, or
# compromised secret store) would silently let a single sluggish or attacker-
# controlled upstream peer hold a worker for ~28 hours. Combined with
# ``VOR_MAX_WORKERS=10`` and the per-run station fan-out, a handful of
# slow-drip responses would exhaust the thread pool and stall the whole feed
# build. The env var stays useful for *tightening* the timeout (e.g. 5s in
# tests with a stub server) but can never raise it above the documented
# Slowloris ceiling.
HTTP_TIMEOUT = min(
    _load_int_env("VOR_HTTP_TIMEOUT", DEFAULT_HTTP_TIMEOUT),
    DEFAULT_HTTP_TIMEOUT,
)
# Security: ``MAX_VOR_FETCH_TIMEOUT`` is the parameter-boundary Slowloris-defence
# ceiling for the public ``fetch_events`` / ``fetch_vor_disruptions`` APIs. The
# env-source clamp on ``HTTP_TIMEOUT`` above bounds operator-controlled config,
# but the public ``timeout`` parameter bypassed it via
# ``timeout or HTTP_TIMEOUT`` at ``_fetch_departure_board_for_station`` — a
# caller passing ``timeout=99999`` (intentional misconfig, leaked CI env,
# compromised secret store, or a hypothetical future ``VOR_FETCH_TIMEOUT`` env
# var wired into a maintenance script) would let a sluggish or attacker-
# controlled VAO peer hold a worker for ~28 hours per fetch, stalling the cron
# pipeline. Capping at the public API entry point (defense-in-depth) means
# every caller — current and future — inherits the ceiling. Same TIGHTEN-only
# contract as ``MAX_OEBB_FETCH_TIMEOUT`` (``src/providers/oebb.py``) and
# ``MAX_WL_FETCH_TIMEOUT`` (``src/providers/wl_fetch.py``) — the parameter-
# boundary defense-in-depth pattern documented in the 2026-05-07 Slowloris-Cap
# Drift Round 4 journal entry, applied to the VOR sibling that round
# explicitly named as still-open ("VOR has env-source cap via
# ``min(VOR_HTTP_TIMEOUT, DEFAULT_HTTP_TIMEOUT)`` but the public
# ``fetch_events(timeout=99999)`` bypasses it via ``timeout or HTTP_TIMEOUT``").
# Cap value matches the VOR-specific 15s ceiling (``DEFAULT_HTTP_TIMEOUT``)
# rather than ``feed_config.MAX_PROVIDER_TIMEOUT`` (25s) because VOR has chosen
# a tighter local Slowloris contract — orchestrator overrides at 25s are
# already documented as needing to be tightened to VOR's 15s ceiling.
# Security: ``DEFAULT_MAX_REQUESTS_PER_DAY`` (100) is the *contractual* hard
# cap of the "VAO Start" tier — exceeding it risks suspension of the access
# ID by the upstream provider. ``_load_int_env`` itself only enforces a
# lower bound (``value > 0``), so a benign-looking env override such as
# ``VOR_MAX_REQUESTS_PER_DAY=99999`` (intentional misconfig, leaked CI env,
# or compromised secret store) would silently disable every quota gate that
# reads this constant (8 sites in this module, plus ``_limit_reached`` in
# ``scripts/update_vor_cache.py``). The env var stays useful for *tightening*
# the budget (e.g. set to 50 during testing), but can never raise it above
# the documented contract limit.
MAX_REQUESTS_PER_DAY = min(
    _load_int_env("VOR_MAX_REQUESTS_PER_DAY", DEFAULT_MAX_REQUESTS_PER_DAY),
    DEFAULT_MAX_REQUESTS_PER_DAY,
)

# How many in-memory increments to accumulate before flushing the quota
# counter to disk. The default of 10 keeps file I/O low (~10x per build)
# while bounding the loss window to 9 requests if the process is killed
# mid-batch. Tests force a flush per call via WIEN_OEPNV_TEST_QUOTA_BATCH.
DEFAULT_QUOTA_FLUSH_BATCH_SIZE = 10
# Security: cap ``VOR_QUOTA_FLUSH_BATCH_SIZE`` at ``MAX_REQUESTS_PER_DAY``
# (the contractual hard cap of 100/day for the VAO Start tier). The batch
# size is the *loss window* if the process is killed abnormally — the
# ``atexit``-registered flush at line 126 does NOT run on SIGKILL / OOM
# kill / kernel panic / container reaper, so any unflushed delta is
# silently dropped. A benign-looking env override such as
# ``VOR_QUOTA_FLUSH_BATCH_SIZE=99999`` (intentional misconfig, leaked CI
# env, or compromised secret store) lets a single run accumulate the
# entire daily quota in memory; one abnormal kill then loses the whole
# count and the next run reads a stale (or zero) on-disk total, allowing
# it to make another 100 requests before the quota gate kicks in — a
# direct breach of the 100/day VAO contract that mirrors the same
# threat model as the previously-fixed ``VOR_MAX_REQUESTS_PER_DAY`` and
# caps. Buffering more than the daily cap
# in memory is by definition wasteful (the per-call fail-fast at
# ``save_request_count`` already blocks at ``MAX_REQUESTS_PER_DAY``), so
# capping at ``MAX_REQUESTS_PER_DAY`` keeps the env useful for tuning
# (operators can lower it for tighter durability) without ever raising
# the loss window above the contract limit.
QUOTA_FLUSH_BATCH_SIZE = max(
    1,
    min(
        _load_int_env("VOR_QUOTA_FLUSH_BATCH_SIZE", DEFAULT_QUOTA_FLUSH_BATCH_SIZE),
        MAX_REQUESTS_PER_DAY,
    ),
)



def _resolve_path(candidate: str | None, *, default: Path) -> Path:
    """
    Resolve a file path from configuration, ensuring it stays within the data directory.

    Protects against Path Traversal by enforcing that the resolved path is relative to ``DATA_DIR``.
    """
    text = (candidate or "").strip()
    if not text:
        return default
    path = Path(text)
    if not path.is_absolute():
        resolved = (BASE_DIR / path).resolve()
    else:
        resolved = path.resolve()

    try:
        resolved.relative_to(DATA_DIR)
    except ValueError:
        _log_warning(
            "Pfad-Traversal erkannt oder Pfad außerhalb von %s: %s. Nutze Standard.",
            DATA_DIR,
            text,
        )
        return default
    return resolved


REQUEST_COUNT_FILE = _resolve_path(
    _get_env("VOR_REQUEST_COUNT_FILE"), default=DATA_DIR / "vor_request_count.json"
)


def _write_request_count_file(path: Path, payload: Mapping[str, Any]) -> None:
    """Atomically write the VOR per-day request-count payload to *path*.

    Security (Trojan-Source / BiDi-Mark Drift Round 11): the file is
    operator-facing diagnostic state, committed to ``main`` by the
    IFTTT-triggered ``update-cycle.yml`` (Stammstrecke step) and by
    the ``update-vor-cache.yml`` operator-only escape hatch (both
    list ``data/vor_request_count.json`` in their ``file_pattern``
    or via ``add_options: -A``) and reviewed via ``cat`` / ``less``
    / the GitHub web UI / IDE preview. ``ensure_ascii=True`` escapes every non-ASCII
    code point as a literal ``\\uXXXX`` sequence, so a future request-
    count payload field carrying station- / provider- / environment-
    controlled content cannot leak the canonical CVE-2021-42574
    Trojan-Source / zero-width / Unicode-line-terminator / 8-bit C1
    union as raw UTF-8 bytes. Mirrors the canonical fix shape pinned
    in PR #1434 / PR #1435 for the sibling ``data/*.json`` sidecar
    writers (``_write_quarantine_file``, ``_save_state``,
    ``_write_heartbeat_file``). Forensic intent is preserved
    (``load_request_count`` recovers the original string from the
    literal escape via ``json.loads``).

    Security (Coordinate finite/range drift, committed-writer
    defence-in-depth): ``allow_nan=False`` mirrors the canonical
    writer-side pin established in Round 1485 at
    :func:`src.places.merge.write_stations` and extended in Round
    1487 to the sibling stations / cache-events writers. The
    payload is a ``Mapping[str, Any]`` so any future field
    (e.g. a fractional response-rate metric, latency average)
    inherits the missing pin and could land non-standard
    ``NaN`` / ``Infinity`` literals (invalid per RFC 8259) in
    the committed ``data/vor_request_count.json`` sidecar.
    """
    with atomic_write(path, mode="w", encoding="utf-8", permissions=0o600) as handle:
        json.dump(payload, handle, ensure_ascii=True, allow_nan=False)
        handle.write("\n")



# Security: per-loader byte caps for the four on-disk parsers in this
# module. Pre-fix every site used the unsafe ``Path.read_text()`` ->
# ``json.loads()`` shape with no size cap whatsoever. A planted huge file
# (compromised CI runner / partial flush + power loss / parallel
# orchestrator's atomic state swap) buffered the entire file into memory
# and propagated ``MemoryError`` (a ``BaseException`` subclass that is NOT
# caught by ``except (FileNotFoundError, OSError, json.JSONDecodeError,
# RecursionError)``) past every catch tuple — crashing the entire VOR
# provider import chain (``_load_station_name_map`` runs at module-import
# time via ``STATION_NAME_MAP = _load_station_name_map()``) or the per-
# request quota debit (``load_request_count`` / ``save_request_count``).
# Each cap is sized at ~100x the largest legitimately-written shape so
# the cap does NOT introduce a false-positive rejection of valid state:
#   - ``vor-haltestellen.mapping.json`` is ~35 KiB at HEAD; 5 MiB is
#     ~143x and accommodates 4-5x growth in upstream VAO catalogue.
#   - ``vor_request_count.json`` is a single small object
#     ``{"date": "...", "requests": N}`` (~50 bytes); 1 MiB matches the
#     existing ``places/quota.py:MAX_QUOTA_FILE_BYTES`` ceiling.
#   - ``vor-haltestellen.csv`` is ~8 KiB at HEAD; 5 MiB is ~625x and
#     accommodates a multi-region catalogue if the VAO scope expands.
# Mirrors the per-loader cap pattern in ``src/utils/cache.py``
# (``MAX_CACHE_FILE_BYTES``) / ``src/places/quota.py``
# (``MAX_QUOTA_FILE_BYTES``) / ``src/places/tiling.py``
# (``MAX_TILE_FILE_BYTES``) — same threat-model bound applied to the
# previously-uncapped VOR provider sites.
MAX_VOR_QUOTA_FILE_BYTES = 1 * 1024 * 1024














# Security: ``VOR_BASE_URL`` (and the legacy ``VOR_BASE`` alias) is the prefix
# that ``VorAuth`` matches with ``r.url.startswith(self.base_url)`` to decide
# whether to attach the VAO ``accessId`` query parameter and the
# ``Authorization: Bearer/Basic <VOR_ACCESS_ID>`` header. ``validate_http_url``
# only checks SSRF/DNS-rebinding properties, not host identity — so an env
# override such as ``VOR_BASE_URL=https://evil.com/api/`` would (a) point all
# VOR fetches at the attacker, and (b) make ``VorAuth`` happily inject the
# access-ID into every one of those requests (URLs starting with the override
# match by definition). Pin the host to the official VAO endpoint, identical
# in shape to ``_validated_oebb_url`` / ``_validated_wl_base`` for the
# corresponding provider env vars. Forks that need a different upstream must
# update this allowlist deliberately rather than via an env override.
#
# 2026-05-10 (HTTPS-only Provider URL Drift): the validator additionally
# pins the scheme to ``https``. ``validate_http_url`` accepts both ``http``
# and ``https``; without this pin, an env override such as
# ``VOR_BASE_URL=http://routenplaner.verkehrsauskunft.at/api/`` would be
# accepted, ``apply_authentication`` would still attach the VAO access ID
# (today only emits a WARNING but proceeds anyway), and every VAO request
# would carry the long-lived credential over plaintext HTTP — captured by
# any on-path attacker (compromised network, BGP hijack, MITM proxy).
# Mirrors the canonical ``validate_public_feed_url`` HTTPS-only pin
# (``src/utils/http.py``) and the OEBB / WL sibling validators.
_VOR_TRUSTED_HOSTS = frozenset({"routenplaner.verkehrsauskunft.at"})


def _validated_vor_base_url(raw: str) -> str | None:
    safe = validate_http_url(raw)
    if not safe:
        return None
    parsed = urlparse(safe)
    # Security: refuse plaintext HTTP — the VAO base URL carries the
    # access ID on every request; an HTTP scheme is a TLS-strip credential
    # leak. See the regex header above for the full threat model.
    if parsed.scheme.lower() != "https":
        return None
    host = (parsed.hostname or "").lower()
    if host not in _VOR_TRUSTED_HOSTS:
        return None
    return safe


def refresh_base_configuration() -> str:
    """
    Refresh VOR base URL and version from environment variables.

    Allows overriding ``VOR_BASE_URL`` and ``VOR_VERSION`` dynamically.
    Sanitizes inputs using ``validate_http_url`` and pins the host to
    the official VAO endpoint via ``_validated_vor_base_url``.
    """
    base_url_env = _get_env("VOR_BASE_URL")
    base_env = _get_env("VOR_BASE")
    version_env = _get_env("VOR_VERSION")
    # Support VOR_VERSIONS as a fallback/alias for the version string
    if not version_env:
        version_env = _get_env("VOR_VERSIONS")

    version = version_env or DEFAULT_VERSION

    # Pre-validate base env vars to avoid injection risks AND pin to the
    # official VAO host so an env override cannot redirect credentials.
    validated_base_url_env = _validated_vor_base_url(base_url_env)
    validated_base_env = _validated_vor_base_url(base_env)
    if base_url_env and not validated_base_url_env:
        _log_warning(
            "VOR_BASE_URL %r ist kein bekannter VAO-Host; verwende Standard.",
            base_url_env,
        )
    if base_env and not validated_base_env:
        _log_warning(
            "VOR_BASE %r ist kein bekannter VAO-Host; verwende Standard.",
            base_env,
        )

    base_url = DEFAULT_BASE_URL

    if validated_base_url_env:
        base_url = validated_base_url_env.rstrip("/") + "/"
        last_segment = base_url.rstrip("/").split("/")[-1]
        if last_segment.startswith("v"):
            version = last_segment
    elif validated_base_env:
        base = validated_base_env.rstrip("/")
        if version_env:
            base_url = f"{base}/{version_env.strip('/')}/"
        else:
            candidate_last = base.split("/")[-1]
            if candidate_last.startswith("v"):
                version = candidate_last
                base_url = base.rstrip("/") + "/"
            else:
                base_url = f"{base}/{version}/"
    else:
        # Fallback to default if envs are invalid or empty
        base_url = f"{DEFAULT_BASE.rstrip('/')}/{version}/"

    global VOR_BASE_URL, VOR_VERSION
    VOR_BASE_URL = base_url
    VOR_VERSION = version
    return VOR_BASE_URL


VOR_BASE_URL = DEFAULT_BASE_URL
VOR_VERSION = DEFAULT_VERSION
refresh_base_configuration()


def _normalise_access_token(raw: str) -> tuple[str, str]:
    token = raw.strip()
    if not token:
        return "", ""

    lower_token = token.lower()

    auth_type_override = os.environ.get('VOR_AUTH_TYPE')
    if auth_type_override:
        auth_type_override = auth_type_override.strip().lower()
        if auth_type_override == "bearer":
            if lower_token.startswith("bearer "):
                normalized = token[7:].strip()
            elif lower_token.startswith("basic "):
                normalized = token[6:].strip()
            else:
                normalized = token
            return normalized, f"Bearer {normalized}"
        elif auth_type_override == "basic":
            if lower_token.startswith("basic "):
                normalized = token[6:].strip()
            elif lower_token.startswith("bearer "):
                normalized = token[7:].strip()
            else:
                normalized = token
            if ":" in normalized:
                encoded = base64.b64encode(normalized.encode("utf-8")).decode("ascii")
                return normalized, f"Basic {encoded}"

            # Check if it's already properly Base64 encoded
            try:
                decoded = base64.b64decode(normalized).decode("utf-8")
                # If it decodes and we can re-encode it to the exact same string, it's valid Base64
                if base64.b64encode(decoded.encode("utf-8")).decode("ascii") == normalized:
                    return normalized, f"Basic {normalized}"
            except Exception: # noqa: S110
                pass  # nosec B110

            # Fallback: forcefully encode it
            encoded = base64.b64encode(normalized.encode("utf-8")).decode("ascii")
            return normalized, f"Basic {encoded}"

    if lower_token.startswith("basic "):
        normalized = token[6:].strip()
        # Heuristic: If it contains a colon, it's likely unencoded user:pass
        if ":" in normalized:
            encoded = base64.b64encode(normalized.encode("utf-8")).decode("ascii")
            return normalized, f"Basic {encoded}"
        return normalized, f"Basic {normalized}"

    if lower_token.startswith("bearer "):
        normalized = token[7:].strip()
        return normalized, f"Bearer {normalized}"

    normalized = token
    if ":" in normalized:
        encoded = base64.b64encode(normalized.encode("utf-8")).decode("ascii")
        header = f"Basic {encoded}"
    else:
        header = f"Bearer {normalized}"
    return normalized, header


def refresh_access_credentials() -> str:
    """
    Reload access credentials from environment variables.

    Supports ``VOR_ACCESS_ID`` (or legacy ``VAO_ACCESS_ID``).
    Automatically detects Basic vs. Bearer tokens.
    """
    raw = read_secret("VOR_ACCESS_ID")
    if not raw:
        raw = read_secret("VAO_ACCESS_ID")
    token, header = _normalise_access_token(raw)

    global VOR_ACCESS_ID, _VOR_ACCESS_TOKEN_RAW, _VOR_AUTHORIZATION_HEADER
    VOR_ACCESS_ID = token
    _VOR_ACCESS_TOKEN_RAW = raw
    _VOR_AUTHORIZATION_HEADER = header
    return VOR_ACCESS_ID


refresh_access_credentials()


class VorAuth(AuthBase):  # type: ignore[misc]
    """
    Injects VOR access credentials into the request via query parameter,
    only if not already authenticated via header.
    """
    def __init__(self, access_id: str, auth_header: str, base_url: str):
        self.access_id = access_id
        self.auth_header = auth_header
        self.base_url = base_url

    def __call__(self, r: requests.PreparedRequest) -> requests.PreparedRequest:
        # Security: Only inject credentials if target is VOR API
        if not r.url or not r.url.startswith(self.base_url):
            return r

        # Inject Authorization header if configured and missing
        if self.auth_header and "Authorization" not in r.headers:
            r.headers["Authorization"] = self.auth_header

        # Inject accessId query param if configured
        if self.access_id:
            # Check if accessId is already present in query params
            try:
                parsed = urlparse(r.url)
                query_params = parse_qsl(parsed.query, keep_blank_values=True)

                if not any(k == "accessId" for k, v in query_params):
                    query_params.append(("accessId", self.access_id))
                    new_query = urlencode(query_params)
                    new_parts = parsed._replace(query=new_query)
                    r.url = urlunparse(new_parts)
            except ValueError:
                pass

        return r


def apply_authentication(session: Session) -> None:
    """
    Configure the requests Session with VOR credentials.

    - Sets the `Authorization` header (if applicable).
    - Assigns VorAuth to session.auth to inject ``accessId`` into query parameters automatically.

    Security (HTTPS-only Provider URL Drift, 2026-05-10): when
    ``VOR_BASE_URL`` carries an ``http://`` scheme, the auth setup
    fails closed — credentials are NOT attached to the session. The
    canonical validator ``_validated_vor_base_url`` already pins the
    scheme to ``https`` at module-load time, but a future caller that
    sets ``vor.VOR_BASE_URL`` directly (test fixture, debug knob,
    refactor regression) could bypass that gate. This second line of
    defence ensures the access ID never reaches the wire over
    plaintext HTTP, even when the validator is bypassed.
    """
    refresh_access_credentials()

    # Security: fail-closed on plaintext HTTP. Pre-fix the code only
    # logged a WARNING but proceeded to attach credentials, which is a
    # fail-OPEN posture: an on-path attacker on the HTTP hop captures
    # the access ID verbatim. Refusing to attach credentials defeats
    # the TLS-strip primitive even if the validator gate is bypassed.
    if VOR_BASE_URL.lower().startswith("http://") and (
        VOR_ACCESS_ID or _VOR_AUTHORIZATION_HEADER
    ):
        _log_warning(
            "VOR_BASE_URL ist http:// — Authentifizierung wird übersprungen, "
            "um den Zugangsschlüssel nicht im Klartext zu übertragen."
        )
        session.headers.setdefault("Accept", "application/json")
        return

    session.headers.setdefault("Accept", "application/json")
    # FIX: Do not set Authorization header directly on session.headers to avoid premature injection
    # Instead pass it to VorAuth which handles conditional injection

    # Use custom AuthBase implementation instead of monkeypatching
    session.auth = VorAuth(VOR_ACCESS_ID, _VOR_AUTHORIZATION_HEADER, VOR_BASE_URL)
























def load_request_count(bypass_cache: bool = False) -> tuple[str | None, int]:
    # Check memory cache first
    vienna_tz = ZoneInfo("Europe/Vienna")
    today_local = datetime.now(vienna_tz).strftime("%Y-%m-%d")

    if not bypass_cache and _QUOTA_CACHE["date"] == today_local:
        # If we have a cached value for today, it might be stale but it's a lower bound.
        # However, for accurate reading we fall through to file.
        return (today_local, _QUOTA_CACHE["count"])

    # Security: ``read_capped_json`` enforces a TOCTOU-safe 1 MiB cap and
    # returns ``None`` for missing / oversized / depth-bombed / corrupt
    # files — closes the ``Path.read_text()`` -> ``MemoryError``
    # propagation that would otherwise escape past the prior catch tuple
    # ``(FileNotFoundError, OSError, json.JSONDecodeError, RecursionError)``
    # (``MemoryError`` is ``BaseException``-rooted and bypasses every
    # subclass-of-``Exception`` catch). ``load_request_count`` is invoked
    # by every VOR fetch in the pipeline; an unbounded read at this site
    # would crash the entire daily quota debit chain.
    data = read_capped_json(
        REQUEST_COUNT_FILE,
        MAX_VOR_QUOTA_FILE_BYTES,
        label="VOR quota",
        logger=log,
    )
    if not isinstance(data, dict):
        return (None, 0)

    stored_date = data.get("date")
    # Using 'requests' key as per new strict schema requirement.
    if stored_date == today_local and "requests" in data:
        count = data["requests"]
        try:
            int_count = int(count)
        except (ValueError, TypeError):
            int_count = 0
        # Security: a poisoned ``data/vor_request_count.json`` (planted by
        # a compromised CI runner, partial flush + power loss, or operator
        # mis-edit) could record a NEGATIVE ``requests`` value. The
        # downstream ``_limit_reached`` check (``scripts/update_vor_cache.py:87``)
        # uses ``todays_count >= MAX_REQUESTS_PER_DAY``, which negative
        # values silently bypass — and ``save_request_count`` perpetuates
        # the negative offset by adding the run's delta to it and writing
        # back. The net effect is that legitimate runs continue to fetch
        # under the projected-usage cap, but the runtime quota counter
        # stays artificially low for many days, breaching defense-in-depth
        # for the contractually-strict VAO Start tier 100/day limit.
        # Clamp at 0 so a tampered file cannot drive the in-memory or
        # on-disk counter below zero; the next save_request_count() flush
        # rewrites the canonical [0, MAX] schema and self-heals.
        int_count = max(0, int_count)

        # Update cache
        _QUOTA_CACHE["date"] = stored_date
        _QUOTA_CACHE["count"] = int_count

        return (stored_date, int_count + _QUOTA_CACHE.get("unsaved_delta", 0))

    # Discard legacy formats (raw integers, 'count' key) or old dates
    return (None, 0)


def _persist_quota_to_disk() -> int:
    """Persist any pending ``unsaved_delta`` to disk; **does not increment**.

    Caller MUST hold :data:`_QUOTA_LOCK`. Returns the new on-disk total
    (or the unchanged in-memory total when there was nothing to flush).

    Split off from :func:`save_request_count` 2026-05-15 to fix a
    quota-inflation bug: the previous :func:`_flush_quota_cache` invoked
    :func:`save_request_count`, which incremented ``unsaved_delta`` by
    one before flushing — every script invocation that made any VOR
    call therefore booked one phantom request beyond the actual API
    traffic. The split makes the persist path callable without that
    side effect while keeping :func:`save_request_count` semantically
    identical for every existing caller (increment-then-conditionally-
    flush). All security invariants (TOCTOU-safe capped read, negative-
    count clamp, atomic write, lock-failure quota-poison sentinel) live
    in this helper now and are exercised through both entry points.
    """
    if _QUOTA_CACHE.get("unsaved_delta", 0) <= 0:
        return cast(int, _QUOTA_CACHE.get("count", 0))

    vienna_tz = ZoneInfo("Europe/Vienna")
    now_local = datetime.now(vienna_tz)
    date_iso = now_local.strftime("%Y-%m-%d")

    # Ensure the parent directory exists before attempting to open the lock file.
    # This prevents FileNotFoundError if the directory structure is missing.
    REQUEST_COUNT_FILE.parent.mkdir(parents=True, exist_ok=True)

    lock_path = REQUEST_COUNT_FILE.with_suffix(".lock")

    try:
        with (
            lock_path.open("a+", encoding="utf-8") as lock_file,
            file_lock(lock_file, exclusive=True),
        ):
            # Security: TOCTOU-safe size cap via read_capped_json.
            disk_date = None
            disk_count = 0
            data = read_capped_json(
                REQUEST_COUNT_FILE,
                MAX_VOR_QUOTA_FILE_BYTES,
                label="VOR quota",
                logger=log,
            )
            if isinstance(data, dict):
                disk_date = data.get("date")
                if "requests" in data:
                    try:
                        disk_count = int(data["requests"])
                    except (ValueError, TypeError):
                        pass

            # Security: clamp at 0 to defeat negative-count quota-bypass
            # (mirrors load_request_count's clamp; see its comment block).
            disk_count = max(0, disk_count)

            if disk_date != date_iso:
                disk_count = 0
                _QUOTA_CACHE["count"] = 0
            else:
                # Update our memory cache to reflect exactly what's on disk right now
                _QUOTA_CACHE["count"] = disk_count

            # Add our unsaved delta to what is on disk.
            new_total = disk_count + _QUOTA_CACHE["unsaved_delta"]

            _QUOTA_CACHE["count"] = new_total
            _QUOTA_CACHE["date"] = date_iso
            _QUOTA_CACHE["unsaved_delta"] = 0

            payload = {"date": date_iso, "requests": new_total}
            try:
                # Centralised atomic + ASCII-safe write —
                # see ``_write_request_count_file`` for the
                # Trojan-Source threat model.
                _write_request_count_file(REQUEST_COUNT_FILE, payload)
            except OSError:
                log.critical("Failed to write to request count file. Quota mechanism poisoned.")
                _QUOTA_CACHE["count"] = MAX_REQUESTS_PER_DAY + 1
                _QUOTA_CACHE["unsaved_delta"] = 0
                return MAX_REQUESTS_PER_DAY + 1
    except (OSError, TimeoutError) as e:
        # Sentinel: file/lock errors can carry attacker-controlled
        # path fragments from a planted lockfile name; sanitise
        # before logging.
        log.warning(
            "Failed to save request count (lock error): %s",
            sanitize_log_arg(str(e)),
        )
        return MAX_REQUESTS_PER_DAY + 1

    return cast(int, new_total)


def save_request_count(now_ignored: datetime | None = None) -> int:
    # We ignore the passed 'now' to enforce UTC consistency internally.
    # But keep the signature compatible if callers pass it.
    # FIX: Use Vienna timezone for day boundaries
    vienna_tz = ZoneInfo("Europe/Vienna")
    now_local = datetime.now(vienna_tz)
    date_iso = now_local.strftime("%Y-%m-%d")

    with _QUOTA_LOCK:
        # Fail-fast check using memory cache
        if _QUOTA_CACHE["date"] == date_iso and _QUOTA_CACHE["count"] + _QUOTA_CACHE["unsaved_delta"] >= MAX_REQUESTS_PER_DAY:
            return cast(int, _QUOTA_CACHE["count"] + _QUOTA_CACHE["unsaved_delta"])

        # Fast path: update memory cache and defer file writes to reduce I/O bottleneck
        if _QUOTA_CACHE["date"] != date_iso:
            _QUOTA_CACHE["date"] = date_iso
            _QUOTA_CACHE["count"] = 0
            _QUOTA_CACHE["unsaved_delta"] = 0

        _QUOTA_CACHE["unsaved_delta"] += 1
        current_total = _QUOTA_CACHE["count"] + _QUOTA_CACHE["unsaved_delta"]

        # Tests set WIEN_OEPNV_TEST_QUOTA_BATCH=1 to force a flush per call.
        batch_limit = 1 if os.getenv("WIEN_OEPNV_TEST_QUOTA_BATCH") == "1" else QUOTA_FLUSH_BATCH_SIZE

        # Only perform expensive file I/O periodically (every X requests) or on the first request
        if current_total == 1 or _QUOTA_CACHE["unsaved_delta"] >= batch_limit or current_total >= MAX_REQUESTS_PER_DAY:
            return _persist_quota_to_disk()

        return cast(int, current_total)












__all__ = [
    "VorAuth",
    "apply_authentication",
    "load_request_count",
    "save_request_count",
    "refresh_access_credentials",
    "refresh_base_configuration",
    "RequestException",
    "ZoneInfo",
]
