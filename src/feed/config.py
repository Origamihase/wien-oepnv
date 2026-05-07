"""Configuration helpers for the feed builder."""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

from ..config.defaults import (
    DEFAULT_ABSOLUTE_MAX_ITEM_AGE_DAYS,
    DEFAULT_DESCRIPTION_CHAR_LIMIT,
    DEFAULT_ENDS_AT_GRACE_MINUTES,
    DEFAULT_FEED_DESCRIPTION,
    DEFAULT_FEED_LINK,
    DEFAULT_FEED_HEALTH_PATH,
    DEFAULT_FEED_HEALTH_JSON_PATH,
    DEFAULT_FEED_TITLE,
    DEFAULT_FEED_TTL_MINUTES,
    DEFAULT_TITLE_CHAR_LIMIT,
    DEFAULT_FRESH_PUBDATE_WINDOW_MIN,
    DEFAULT_MAX_ITEMS,
    DEFAULT_MAX_ITEM_AGE_DAYS,
    DEFAULT_OUT_PATH,
    DEFAULT_PAGES_BASE_URL,
    DEFAULT_CACHE_MAX_AGE_HOURS,
    DEFAULT_PROVIDER_MAX_WORKERS,
    DEFAULT_PROVIDER_TIMEOUT,
    DEFAULT_STATE_PATH,
    DEFAULT_STATE_RETENTION_DAYS,
)
from ..utils.env import get_bool_env, get_int_env
from ..utils.http import validate_public_feed_url

ALLOWED_ROOTS = {"docs", "data", "log"}
REPO_ROOT = Path(__file__).resolve().parents[2]
LOG_TIMEZONE = ZoneInfo("Europe/Vienna")
log = logging.getLogger(__name__)

# Security: ``MAX_PROVIDER_TIMEOUT`` is the Slowloris-defence ceiling for the
# orchestrator's per-provider fetch budget. ``feed_config.PROVIDER_TIMEOUT``
# (and per-provider overrides like ``PROVIDER_TIMEOUT_VOR`` resolved by
# ``build_feed._provider_timeout_override``) is consumed by ``build_feed.py``
# as both (a) the per-fetch HTTP timeout passed to provider fetch callables
# and (b) the deadline on each ``ThreadPoolExecutor`` future. ``get_int_env``
# only enforced a non-negative lower bound, so a benign-looking env override
# such as ``PROVIDER_TIMEOUT=99999`` (intentional misconfig, leaked CI env,
# compromised secret store) would silently let a sluggish or attacker-
# controlled upstream peer hold a worker for ~28 hours per fetch, stalling
# the whole feed-build cron. The cap can only TIGHTEN — env overrides may
# lower the timeout (tests use 1–5s) but never raise it above the documented
# ceiling. Mirrors the ``min(VOR_HTTP_TIMEOUT, DEFAULT_HTTP_TIMEOUT)`` cap in
# ``src/providers/vor.py`` and the ``MAX_TIMEOUT_S`` enforcement in
# ``GooglePlacesConfig.__post_init__`` (``src/places/client.py``).
MAX_PROVIDER_TIMEOUT = DEFAULT_PROVIDER_TIMEOUT

# Security: ``MAX_LOG_BYTES`` is the disk-exhaustion-defence ceiling for the
# rotating-log size. ``LOG_MAX_BYTES`` is consumed by the two
# ``RotatingFileHandler`` instances in ``src/feed/logging.py`` (``errors.log``
# and ``diagnostics.log``) as the size threshold that triggers rotation;
# ``get_int_env`` only enforced a non-negative lower bound, so a benign-
# looking env override such as ``LOG_MAX_BYTES=999999999999`` (intentional
# misconfig, leaked CI env, compromised secret store) would prevent
# rotation entirely and let the active log file grow until the volume
# fills, stalling the cron pipeline (write failures crash subsequent
# ``configure_logging`` calls and any provider that emits a log line on
# the failure path). The cap is intentionally generous (100x default) so
# operators can absorb verbose-debug runs without raising the ceiling, but
# the absolute upper bound bounds the worst-case disk footprint at
# ``2 * MAX_LOG_BYTES * (MAX_LOG_BACKUP_COUNT + 1)`` (two log files share
# the threshold). Mirrors the TIGHTEN-only contract of ``MAX_PROVIDER_TIMEOUT``
# above and ``MAX_TIMEOUT_S`` / ``MAX_REQUEST_RETRIES`` in
# ``src/places/client.py``.
DEFAULT_LOG_MAX_BYTES = 1_000_000
MAX_LOG_BYTES = 100 * 1024 * 1024

# Security: ``MAX_LOG_BACKUP_COUNT`` is the disk-exhaustion-defence ceiling for
# the number of rotated log files retained per ``RotatingFileHandler`` (one for
# ``errors.log``, one for ``diagnostics.log``). ``LOG_BACKUP_COUNT`` was the
# uncapped sibling of ``LOG_MAX_BYTES`` — Round 6 capped per-file size at
# ``MAX_LOG_BYTES`` but left the *multiplier* in the worst-case formula
# ``2 * MAX_LOG_BYTES * (LOG_BACKUP_COUNT + 1)`` unbounded. A benign-looking
# env override such as ``LOG_BACKUP_COUNT=999999`` (intentional misconfig,
# leaked CI env, compromised secret store) would let one operator override
# defeat Round 6's per-file ceiling: with the 100MB cap and a million backups
# the worst-case disk footprint is ~190 TB regardless of the ``LOG_MAX_BYTES``
# clamp. The cap is intentionally generous (100x default) so operators can
# extend retention for forensics without raising the ceiling, but the upper
# bound keeps the worst-case footprint at ``2 * 100 MiB * 501 ≈ 100 GiB`` even
# with both env overrides at their post-clamp maxima — bounded for any CI
# runner volume. TIGHTEN-only contract mirrors ``MAX_LOG_BYTES`` above.
DEFAULT_LOG_BACKUP_COUNT = 5
MAX_LOG_BACKUP_COUNT = 500

# Security: ``MAX_STATE_RETENTION_DAYS`` is the retention-window ceiling for
# the ``first_seen`` state file. ``STATE_RETENTION_DAYS`` is consumed in
# ``build_feed._load_state`` as ``now_utc - timedelta(days=STATE_RETENTION_DAYS)``
# to discard entries older than the window. ``get_int_env`` only enforces a
# non-negative lower bound, so a benign-looking env override such as
# ``STATE_RETENTION_DAYS=99999999`` (intentional misconfig, leaked CI env,
# compromised secret store) would (a) raise ``OverflowError: date value out
# of range`` from the ``datetime - timedelta`` arithmetic — Python's datetime
# is bounded at year 1, so subtracting 99999999 days underflows — propagating
# out of ``_load_state`` past the ``except FileNotFoundError, JSONDecodeError``
# / generic ``except Exception`` handlers and crashing the entire feed-build
# pipeline; and (b) at non-overflow values (e.g. 10000 days ≈ 27 years),
# disable the retention cutoff so the on-disk state file grows unboundedly
# with every new RSS item the providers emit, eventually exhausting the disk
# and stalling the cron job that writes it. The cap is intentionally generous
# (~60x default) so operators can extend retention for long-running RSS
# subscribers without raising the ceiling; ten years is well within Python's
# datetime range and bounds the on-disk state file size to a finite multiple
# of the per-day item-emission rate. TIGHTEN-only contract mirrors
# ``MAX_LOG_BYTES`` and ``MAX_LOG_BACKUP_COUNT`` above.
MAX_STATE_RETENTION_DAYS = 3650


class InvalidPathError(ValueError):
    """Raised when a configured path is outside the permitted directories."""


# Security: pin ``FEED_LINK`` and ``PAGES_BASE_URL`` to GitHub-hosted domains.
# Both env vars are interpolated into the public RSS feed (channel ``<link>``,
# per-item ``<link>`` fallback, atom self/alternate hrefs). The host pin is
# implemented by ``validate_public_feed_url`` (in ``src.utils.http``) so the
# same allowlist is shared with other publishing surfaces (e.g. the sitemap
# generator) and a future fourth feed-output URL inherits the pin without
# anyone having to remember to add it. Module-local alias preserves the
# historical test surface (``feed_config._validated_feed_public_url``).
_validated_feed_public_url = validate_public_feed_url


def validate_path(path: Path, name: str) -> Path:
    """Ensure ``path`` stays within whitelisted directories."""

    resolved = path.resolve()
    bases = {REPO_ROOT}
    if "PYTEST_CURRENT_TEST" in os.environ:
        bases.add(Path.cwd().resolve())

    for base in bases:
        try:
            rel = resolved.relative_to(base)
        except Exception as rel_exc:
            log.debug("Failed to resolve relative path", exc_info=rel_exc)
            continue
        if rel.parts and rel.parts[0] in ALLOWED_ROOTS:
            return resolved
    raise InvalidPathError(f"{name} outside allowed directories")


def resolve_env_path(env_name: str, default: str | Path, *, allow_fallback: bool = False) -> Path:
    """Return a repository-internal path for ``env_name``."""

    default_path = Path(default)
    raw = os.getenv(env_name)
    candidate_str = (raw or "").strip()

    if not candidate_str:
        validate_path(default_path, env_name)
        resolved_default = Path(default_path)
        return resolved_default

    candidate_path = Path(candidate_str)
    try:
        resolved = validate_path(candidate_path, env_name)
    except ValueError:
        if not allow_fallback:
            raise

        default_parts = Path(default_path).parts
        candidate_parts = candidate_path.parts
        if default_parts and len(candidate_parts) >= len(default_parts):
            if candidate_parts[-len(default_parts):] == default_parts:
                validate_path(default_path, env_name)
                fallback = Path(default_path)
                return fallback

        validate_path(default_path, env_name)
        fallback_path = Path(default_path)
        return fallback_path
    return resolved


@dataclass(frozen=True)
class FeedPaths:
    """Resolved file-system paths used by the feed builder."""

    log_dir: Path
    out_path: Path
    state_file: Path


@dataclass(frozen=True)
class FeedSettings:
    """Key feed builder settings derived from environment variables."""

    feed_title: str
    feed_link: str
    feed_description: str
    feed_ttl: int
    title_char_limit: int
    description_char_limit: int
    fresh_pubdate_window_min: int
    max_items: int
    max_item_age_days: int
    absolute_max_age_days: int
    ends_at_grace_minutes: int
    provider_timeout: int
    provider_max_workers: int
    state_retention_days: int


LOG_LEVEL: str = "INFO"
LOG_FORMAT: str = "plain"
LOG_DIR_PATH: Path = Path("log")
LOG_MAX_BYTES: int = DEFAULT_LOG_MAX_BYTES
LOG_BACKUP_COUNT: int = DEFAULT_LOG_BACKUP_COUNT
OUT_PATH: Path = DEFAULT_OUT_PATH
FEED_HEALTH_PATH: Path = DEFAULT_FEED_HEALTH_PATH
FEED_HEALTH_JSON_PATH: Path = DEFAULT_FEED_HEALTH_JSON_PATH
FEED_TITLE: str = DEFAULT_FEED_TITLE
FEED_LINK: str = DEFAULT_FEED_LINK
PAGES_BASE_URL: str = DEFAULT_PAGES_BASE_URL
FEED_DESC: str = DEFAULT_FEED_DESCRIPTION
FEED_TTL: int = DEFAULT_FEED_TTL_MINUTES
TITLE_CHAR_LIMIT: int = DEFAULT_TITLE_CHAR_LIMIT
DESCRIPTION_CHAR_LIMIT: int = DEFAULT_DESCRIPTION_CHAR_LIMIT
FRESH_PUBDATE_WINDOW_MIN: int = DEFAULT_FRESH_PUBDATE_WINDOW_MIN
MAX_ITEMS: int = DEFAULT_MAX_ITEMS
MAX_ITEM_AGE_DAYS: int = DEFAULT_MAX_ITEM_AGE_DAYS
ABSOLUTE_MAX_AGE_DAYS: int = DEFAULT_ABSOLUTE_MAX_ITEM_AGE_DAYS
ENDS_AT_GRACE_MINUTES: int = DEFAULT_ENDS_AT_GRACE_MINUTES
CACHE_MAX_AGE_HOURS: int = DEFAULT_CACHE_MAX_AGE_HOURS
PROVIDER_TIMEOUT: int = DEFAULT_PROVIDER_TIMEOUT
PROVIDER_MAX_WORKERS: int = DEFAULT_PROVIDER_MAX_WORKERS
STATE_FILE: Path = DEFAULT_STATE_PATH
STATE_RETENTION_DAYS: int = DEFAULT_STATE_RETENTION_DAYS


def _load_from_env() -> None:
    global LOG_LEVEL, LOG_FORMAT, LOG_DIR_PATH, LOG_MAX_BYTES, LOG_BACKUP_COUNT
    global OUT_PATH, FEED_HEALTH_PATH, FEED_HEALTH_JSON_PATH, FEED_TITLE, FEED_LINK, PAGES_BASE_URL, FEED_DESC, FEED_TTL
    global TITLE_CHAR_LIMIT, DESCRIPTION_CHAR_LIMIT, FRESH_PUBDATE_WINDOW_MIN, MAX_ITEMS
    global MAX_ITEM_AGE_DAYS, ABSOLUTE_MAX_AGE_DAYS, ENDS_AT_GRACE_MINUTES
    global PROVIDER_TIMEOUT, PROVIDER_MAX_WORKERS, STATE_FILE, STATE_RETENTION_DAYS
    global CACHE_MAX_AGE_HOURS

    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    LOG_FORMAT = os.getenv("LOG_FORMAT", "plain").strip().lower()
    LOG_DIR_PATH = resolve_env_path("LOG_DIR", Path("log"), allow_fallback=True)
    # Security: clamp the env override to ``MAX_LOG_BYTES`` to defeat the
    # disk-exhaustion vector documented at the constant declaration above.
    LOG_MAX_BYTES = min(
        max(get_int_env("LOG_MAX_BYTES", DEFAULT_LOG_MAX_BYTES), 0),
        MAX_LOG_BYTES,
    )
    # Security: clamp the env override to ``MAX_LOG_BACKUP_COUNT`` to defeat
    # the disk-exhaustion vector documented at the constant declaration above.
    # Without the cap a single ``LOG_BACKUP_COUNT=999999`` would multiply the
    # per-file ``MAX_LOG_BYTES`` ceiling by an unbounded factor and re-enable
    # the disk-fill scenario Round 6 fixed for ``LOG_MAX_BYTES``.
    LOG_BACKUP_COUNT = min(
        max(get_int_env("LOG_BACKUP_COUNT", DEFAULT_LOG_BACKUP_COUNT), 0),
        MAX_LOG_BACKUP_COUNT,
    )

    OUT_PATH = resolve_env_path("OUT_PATH", DEFAULT_OUT_PATH)
    FEED_HEALTH_PATH = resolve_env_path(
        "FEED_HEALTH_PATH", DEFAULT_FEED_HEALTH_PATH, allow_fallback=True
    )
    FEED_HEALTH_JSON_PATH = resolve_env_path(
        "FEED_HEALTH_JSON_PATH", DEFAULT_FEED_HEALTH_JSON_PATH, allow_fallback=True
    )
    FEED_TITLE = os.getenv("FEED_TITLE", DEFAULT_FEED_TITLE)
    raw_feed_link = os.getenv("FEED_LINK", DEFAULT_FEED_LINK)
    # Security: pin to GitHub-hosted domains (see ``_validated_feed_public_url``)
    # so an env override cannot weaponise the public feed as a phishing redirect.
    validated_feed_link = _validated_feed_public_url(raw_feed_link)
    if not validated_feed_link:
        validated_feed_link = _validated_feed_public_url(DEFAULT_FEED_LINK) or DEFAULT_FEED_LINK
        if raw_feed_link.strip() and raw_feed_link.strip() != DEFAULT_FEED_LINK:
            log.warning(
                "FEED_LINK %r is not a known GitHub host; falling back to default.",
                raw_feed_link,
            )
    FEED_LINK = validated_feed_link
    raw_pages_base = os.getenv("PAGES_BASE_URL", DEFAULT_PAGES_BASE_URL)
    validated_pages_base = _validated_feed_public_url(raw_pages_base)
    if not validated_pages_base:
        validated_pages_base = (
            _validated_feed_public_url(DEFAULT_PAGES_BASE_URL) or DEFAULT_PAGES_BASE_URL
        )
        if raw_pages_base.strip() and raw_pages_base.strip() != DEFAULT_PAGES_BASE_URL:
            log.warning(
                "PAGES_BASE_URL %r is not a known GitHub host; falling back to default.",
                raw_pages_base,
            )
    # Normalise the hostname to lowercase so feeds built on forks with
    # mixed-case repository owners (e.g. ``Origamihase``) emit canonical
    # URLs that GitHub Pages serves without redirect. The path component
    # is preserved verbatim because GitHub Pages treats paths as
    # case-sensitive.
    parsed_pages_base = urlparse(validated_pages_base)
    if parsed_pages_base.hostname:
        new_netloc = parsed_pages_base.hostname.lower()
        if parsed_pages_base.port is not None:
            new_netloc = f"{new_netloc}:{parsed_pages_base.port}"
        validated_pages_base = urlunparse(parsed_pages_base._replace(netloc=new_netloc))
    PAGES_BASE_URL = validated_pages_base.rstrip("/")
    FEED_DESC = os.getenv("FEED_DESC", DEFAULT_FEED_DESCRIPTION)
    FEED_TTL = max(get_int_env("FEED_TTL", DEFAULT_FEED_TTL_MINUTES), 0)
    TITLE_CHAR_LIMIT = max(
        get_int_env("FEED_TITLE_CHAR_LIMIT", DEFAULT_TITLE_CHAR_LIMIT), 0
    )
    DESCRIPTION_CHAR_LIMIT = max(
        get_int_env("DESCRIPTION_CHAR_LIMIT", DEFAULT_DESCRIPTION_CHAR_LIMIT), 0
    )
    FRESH_PUBDATE_WINDOW_MIN = max(
        get_int_env("FRESH_PUBDATE_WINDOW_MIN", DEFAULT_FRESH_PUBDATE_WINDOW_MIN), 0
    )
    MAX_ITEMS = max(get_int_env("MAX_ITEMS", DEFAULT_MAX_ITEMS), 0)
    MAX_ITEM_AGE_DAYS = max(
        get_int_env("MAX_ITEM_AGE_DAYS", DEFAULT_MAX_ITEM_AGE_DAYS), 0
    )
    ABSOLUTE_MAX_AGE_DAYS = max(
        get_int_env("ABSOLUTE_MAX_AGE_DAYS", DEFAULT_ABSOLUTE_MAX_ITEM_AGE_DAYS), 0
    )
    ENDS_AT_GRACE_MINUTES = max(
        get_int_env("ENDS_AT_GRACE_MINUTES", DEFAULT_ENDS_AT_GRACE_MINUTES), 0
    )
    CACHE_MAX_AGE_HOURS = max(
        get_int_env("CACHE_MAX_AGE_HOURS", DEFAULT_CACHE_MAX_AGE_HOURS), 0
    )
    # Security: clamp the env override to ``MAX_PROVIDER_TIMEOUT`` to defeat
    # the Slowloris vector documented at the constant declaration above.
    PROVIDER_TIMEOUT = min(
        max(get_int_env("PROVIDER_TIMEOUT", DEFAULT_PROVIDER_TIMEOUT), 0),
        MAX_PROVIDER_TIMEOUT,
    )
    PROVIDER_MAX_WORKERS = max(
        get_int_env("PROVIDER_MAX_WORKERS", DEFAULT_PROVIDER_MAX_WORKERS), 0
    )
    STATE_FILE = resolve_env_path("STATE_PATH", DEFAULT_STATE_PATH)
    # Security: clamp the env override to ``MAX_STATE_RETENTION_DAYS`` to defeat
    # the OverflowError / disk-exhaustion vector documented at the constant
    # declaration above. Without the cap a single ``STATE_RETENTION_DAYS=99999999``
    # would crash ``_load_state`` via ``datetime - timedelta`` underflow and
    # halt the feed-build pipeline.
    STATE_RETENTION_DAYS = min(
        max(get_int_env("STATE_RETENTION_DAYS", DEFAULT_STATE_RETENTION_DAYS), 0),
        MAX_STATE_RETENTION_DAYS,
    )


_load_from_env()


def refresh_from_env() -> None:
    """Re-evaluate all feed configuration values from environment variables."""

    _load_from_env()

RFC = "%a, %d %b %Y %H:%M:%S %z"


def build_paths() -> FeedPaths:
    """Return the resolved filesystem paths for the current environment."""

    return FeedPaths(
        log_dir=LOG_DIR_PATH,
        out_path=OUT_PATH,
        state_file=STATE_FILE,
    )


def build_settings() -> FeedSettings:
    """Assemble the active feed settings based on environment variables."""

    return FeedSettings(
        feed_title=FEED_TITLE,
        feed_link=FEED_LINK,
        feed_description=FEED_DESC,
        feed_ttl=FEED_TTL,
        title_char_limit=TITLE_CHAR_LIMIT,
        description_char_limit=DESCRIPTION_CHAR_LIMIT,
        fresh_pubdate_window_min=FRESH_PUBDATE_WINDOW_MIN,
        max_items=MAX_ITEMS,
        max_item_age_days=MAX_ITEM_AGE_DAYS,
        absolute_max_age_days=ABSOLUTE_MAX_AGE_DAYS,
        ends_at_grace_minutes=ENDS_AT_GRACE_MINUTES,
        provider_timeout=PROVIDER_TIMEOUT,
        provider_max_workers=PROVIDER_MAX_WORKERS,
        state_retention_days=STATE_RETENTION_DAYS,
    )


__all__ = [
    "ABSOLUTE_MAX_AGE_DAYS",
    "DESCRIPTION_CHAR_LIMIT",
    "ENDS_AT_GRACE_MINUTES",
    "CACHE_MAX_AGE_HOURS",
    "FEED_DESC",
    "FEED_HEALTH_PATH",
    "FEED_HEALTH_JSON_PATH",
    "FEED_LINK",
    "FEED_TITLE",
    "FEED_TTL",
    "FeedPaths",
    "FeedSettings",
    "FRESH_PUBDATE_WINDOW_MIN",
    "LOG_BACKUP_COUNT",
    "LOG_DIR_PATH",
    "LOG_FORMAT",
    "LOG_LEVEL",
    "LOG_MAX_BYTES",
    "LOG_TIMEZONE",
    "MAX_ITEM_AGE_DAYS",
    "MAX_ITEMS",
    "MAX_LOG_BACKUP_COUNT",
    "MAX_LOG_BYTES",
    "MAX_PROVIDER_TIMEOUT",
    "MAX_STATE_RETENTION_DAYS",
    "OUT_PATH",
    "PAGES_BASE_URL",
    "PROVIDER_MAX_WORKERS",
    "PROVIDER_TIMEOUT",
    "RFC",
    "STATE_FILE",
    "STATE_RETENTION_DAYS",
    "TITLE_CHAR_LIMIT",
    "build_paths",
    "build_settings",
    "get_bool_env",
    "get_int_env",
    "refresh_from_env",
    "resolve_env_path",
    "validate_path",
]
