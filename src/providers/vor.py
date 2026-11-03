"""
VOR/VAO Provider Module.

This module implements the logic to fetch transport alerts from the VOR (Verkehrsverbund Ost-Region)
/ VAO (Verkehrsauskunft Österreich) API. It handles:
- Authentication (Access ID / Token injection)
- Station resolution (Name -> ID)
- Rate limiting and caching of request counts
- Safe HTTP fetching with retries and DoS protection
- Parsing of complex JSON responses into standardized feed items.

Configuration is primarily driven by environment variables (e.g.,
``VOR_ACCESS_ID``, ``VOR_HTTP_TIMEOUT``, ``VOR_MAX_REQUESTS_PER_DAY``).

Scope: since the operator policy of 2026-05-11 the VOR/VAO API is used for
the S-Bahn-Stammstrecke monitor only — ``scripts/update_stammstrecke_status.py``
is this module's sole consumer, and it names its stops directly. There is no
repository-wide VOR station list any more; the ``VOR_STATION_IDS`` variable
this docstring used to advertise was never read by this module and its
directory-derived default (``src.utils.stations.vor_station_ids``) was removed
in 2026-09-13 as dead code.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from requests import RequestException, Session

from zoneinfo import ZoneInfo

from ..utils.env import read_secret
from ..utils.files import loads_finite
from ..utils.http import (
    request_safe,
    session_with_retries,
)
from ..utils.logging import sanitize_log_arg, sanitize_log_message
from ..utils.vao_version import ListedVersion, is_version, load_state, parse_version_list
from .vor_env import BASE_DIR, DATA_DIR, _get_env
from .vor_auth import (
    VorAuth,
    _VOR_TRUSTED_HOSTS,
    _normalise_access_token,
    _validated_vor_base_url,
)

log = logging.getLogger(__name__)

# VAO API version (Michael, 24.10.2026: "Wir können die VAO-Version gerne
# anheben"): the newest version the VAO lists at
# https://routenplaner.verkehrsauskunft.at/vao/restproxy. That was v1.13.0
# from 24.10.2026 until the VAO withdrew it on 28.10.2026 between 14:30 and
# 15:00 (Vienna): since then the list holds v1.11.0 (until 31.05.2027) and
# v1.12.0 (until 30.08.2027), and every v1.13.0 request answers HTTP 404. The
# same /departureBoard query against v1.11.0, v1.12.0 and v1.13.0 gave
# identical departures and Stammstrecke observations on 24.10.2026.
# Since 2026-10-29 the Stammstrecke monitor picks the version from the VAO
# list on every poll (src/utils/vao_version.py, operator decision: "Lies
# automatisch aus, was die neuste Version ist. Wenn sie nicht funktioniert,
# nimm automatisch die davor"); this default only serves a checkout without
# cache/stammstrecke/vao_version.json and a poll that cannot read the list.
DEFAULT_VERSION = "v1.12.0"
DEFAULT_BASE = "https://routenplaner.verkehrsauskunft.at/vao/restproxy"
#: The VAO version list: JSON, no access key, not counted against the budget.
VERSION_LIST_URL = DEFAULT_BASE
VERSION_LIST_TIMEOUT = 15.0
DEFAULT_BASE_URL = f"{DEFAULT_BASE}/{DEFAULT_VERSION}/"
DEFAULT_USER_AGENT = "wien-oepnv/1.0 (+https://github.com/Origamihase/wien-oepnv)"

DEFAULT_HTTP_TIMEOUT = 15
# "VAO Start" contract limit: 100 requests per day (hard limit).
DEFAULT_MAX_REQUESTS_PER_DAY = 100

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

VOR_ACCESS_ID = ""
_VOR_ACCESS_TOKEN_RAW = ""  # nosec B105
_VOR_AUTHORIZATION_HEADER = ""


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


# Long enough for the messages ``request_safe`` and the VAO scripts raise (an
# ``errorCode`` with its ``errorText``, a wrong Content-Type, an HTTP status
# line), short enough for one log line and the Feed-Wächter issue.
FAILURE_REASON_MAX_CHARS = 240


def describe_failure(exc: BaseException) -> str:
    """The exception type and its message, masked, for a log line or status.

    The type alone ("ValueError", "HTTPError") cannot tell an ``errorCode``
    envelope from a wrong Content-Type, a refused key from a server error: on
    21.10.2026 17:31-20:01 and from 28.10.2026 15:00 the Stammstrecke log
    showed nothing else (operator decision 2026-10-28: show the reason).
    ``VorAuth`` puts the ``accessId`` into the prepared URL and a
    ``RequestException`` can carry that URL, so the message goes through
    :func:`_sanitize_message`, which masks the secret itself and every
    credential-named query parameter, and is cut to
    :data:`FAILURE_REASON_MAX_CHARS`.
    """

    message = _sanitize_message(str(exc)).strip()
    reason = f"{type(exc).__name__}: {message}" if message else type(exc).__name__
    if len(reason) > FAILURE_REASON_MAX_CHARS:
        reason = reason[: FAILURE_REASON_MAX_CHARS - 1] + "…"
    return reason


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


# Security: ``DEFAULT_HTTP_TIMEOUT`` (15s) is the Slowloris-defence ceiling for
# every VOR request — both the connect and read budget for VOR's HTTP calls
# (``fetch_content_safe`` in :mod:`src.utils.http`), plus the cache-update
# script at ``scripts/update_vor_stations.py``. ``_load_int_env`` only enforces a lower
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
# ``atexit``-registered flush (``_flush_quota_cache``) does NOT run on SIGKILL / OOM
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


from .vor_quota import (  # noqa: E402
    _flush_quota_cache,
    _write_request_count_file,
    load_request_count,
    reserve_request_slot,
    save_request_count,
)


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


def version_pinned_by_env() -> bool:
    """True when ``VOR_BASE_URL``, ``VOR_BASE`` or ``VOR_VERSION`` sets the version.

    An operator pin wins over the automatic choice from the VAO list.
    """
    return any(_get_env(name) for name in ("VOR_BASE_URL", "VOR_BASE", "VOR_VERSION", "VOR_VERSIONS"))


def use_version(version: str) -> str:
    """Point :data:`VOR_BASE_URL` at *version* of the official VAO endpoint.

    Ignored when the environment pins the version or *version* is not a
    version string; returns the version in use afterwards.
    """
    global VOR_BASE_URL, VOR_VERSION
    if version_pinned_by_env() or not is_version(version):
        return VOR_VERSION
    VOR_BASE_URL = f"{DEFAULT_BASE.rstrip('/')}/{version}/"
    VOR_VERSION = version
    return VOR_VERSION


def use_stored_version(path: Path | None = None) -> str:
    """Use the version the Stammstrecke monitor chose last (``cache/stammstrecke/vao_version.json``).

    For the probes outside the monitor (``verify_vor_access_id.py``,
    ``check_vor_auth.py``), so they ask the version the monitor polls.
    """
    version = load_state(path).version
    return use_version(version) if version is not None else VOR_VERSION


def fetch_version_list(timeout: float = VERSION_LIST_TIMEOUT) -> list[ListedVersion] | None:
    """The versions the VAO lists, newest first; ``None`` when the list cannot be read.

    A plain session without :class:`VorAuth`: the list needs no access key, so
    the key never travels and the request does not count against the budget.
    """
    try:
        with session_with_retries(VOR_USER_AGENT, **VOR_RETRY_OPTIONS) as session:
            response = request_safe(
                session,
                VERSION_LIST_URL,
                timeout=timeout,
                allowed_content_types=("application/json",),
                headers={"Accept": "application/json"},
            )
            payload = loads_finite(response.content)
    except Exception as exc:
        _log_warning("VAO-Versionsliste nicht lesbar: %s", describe_failure(exc))
        return None
    listed = parse_version_list(payload)
    if not listed:
        _log_warning("VAO-Versionsliste ohne gültige Version.")
        return None
    return listed


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


__all__ = [
    "VorAuth",
    "apply_authentication",
    "load_request_count",
    "reserve_request_slot",
    "save_request_count",
    "refresh_access_credentials",
    "refresh_base_configuration",
    "RequestException",
    "ZoneInfo",
    # Defined here, read by the scripts, src.stammstrecke and the tests.
    "log",
    "BASE_DIR",
    "DATA_DIR",
    "DEFAULT_VERSION",
    "DEFAULT_BASE",
    "VERSION_LIST_URL",
    "VERSION_LIST_TIMEOUT",
    "DEFAULT_BASE_URL",
    "DEFAULT_USER_AGENT",
    "DEFAULT_HTTP_TIMEOUT",
    "DEFAULT_MAX_REQUESTS_PER_DAY",
    "VOR_USER_AGENT",
    "VOR_RETRY_OPTIONS",
    "VOR_ACCESS_ID",
    "_VOR_AUTHORIZATION_HEADER",
    "FAILURE_REASON_MAX_CHARS",
    "describe_failure",
    "_sanitize_message",
    "_log_warning",
    "_get_env",
    "_load_int_env",
    "_resolve_path",
    "HTTP_TIMEOUT",
    "MAX_REQUESTS_PER_DAY",
    "DEFAULT_QUOTA_FLUSH_BATCH_SIZE",
    "QUOTA_FLUSH_BATCH_SIZE",
    "VOR_BASE_URL",
    "VOR_VERSION",
    "version_pinned_by_env",
    "use_version",
    "use_stored_version",
    "fetch_version_list",
    "request_safe",
    # Re-exported from src.providers.vor_auth.
    "_VOR_TRUSTED_HOSTS",
    "_validated_vor_base_url",
    "_normalise_access_token",
    # Re-exported from src.providers.vor_quota.
    "_flush_quota_cache",
    "_write_request_count_file",
]
