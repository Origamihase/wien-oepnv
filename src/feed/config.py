"""Configuration helpers for the feed builder."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

try:  # pragma: no cover - support package and script execution
    from config.defaults import (
        DEFAULT_ABSOLUTE_MAX_ITEM_AGE_DAYS,
        DEFAULT_DESCRIPTION_CHAR_LIMIT,
        DEFAULT_ENDS_AT_GRACE_MINUTES,
        DEFAULT_FEED_DESCRIPTION,
        DEFAULT_FEED_LINK,
        DEFAULT_FEED_HEALTH_PATH,
        DEFAULT_FEED_HEALTH_JSON_PATH,
        DEFAULT_FEED_TITLE,
        DEFAULT_FEED_TTL_MINUTES,
        DEFAULT_FRESH_PUBDATE_WINDOW_MIN,
        DEFAULT_MAX_ITEMS,
        DEFAULT_MAX_ITEM_AGE_DAYS,
        DEFAULT_OUT_PATH,
        DEFAULT_CACHE_MAX_AGE_HOURS,
        DEFAULT_PROVIDER_MAX_WORKERS,
        DEFAULT_PROVIDER_TIMEOUT,
        DEFAULT_STATE_PATH,
        DEFAULT_STATE_RETENTION_DAYS,
    )
    from utils.env import get_bool_env, get_int_env
except ModuleNotFoundError:  # pragma: no cover
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
        DEFAULT_FRESH_PUBDATE_WINDOW_MIN,
        DEFAULT_MAX_ITEMS,
        DEFAULT_MAX_ITEM_AGE_DAYS,
        DEFAULT_OUT_PATH,
        DEFAULT_CACHE_MAX_AGE_HOURS,
        DEFAULT_PROVIDER_MAX_WORKERS,
        DEFAULT_PROVIDER_TIMEOUT,
        DEFAULT_STATE_PATH,
        DEFAULT_STATE_RETENTION_DAYS,
    )
    from ..utils.env import get_bool_env, get_int_env

ALLOWED_ROOTS = {"docs", "data", "log"}
REPO_ROOT = Path(__file__).resolve().parents[2]
LOG_TIMEZONE = ZoneInfo("Europe/Vienna")


class InvalidPathError(ValueError):
    """Raised when a configured path is outside the permitted directories."""


def validate_path(path: Path, name: str) -> Path:
    """Ensure ``path`` stays within whitelisted directories."""

    resolved = path.resolve()
    bases = {Path.cwd().resolve(), REPO_ROOT}
    for base in bases:
        try:
            rel = resolved.relative_to(base)
        except Exception:
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
        os.environ[env_name] = resolved_default.as_posix()
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
                os.environ[env_name] = fallback.as_posix()
                return fallback

        validate_path(default_path, env_name)
        fallback_path = Path(default_path)
        os.environ[env_name] = fallback_path.as_posix()
        return fallback_path
    os.environ[env_name] = resolved.as_posix()
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
    description_char_limit: int
    fresh_pubdate_window_min: int
    max_items: int
    max_item_age_days: int
    absolute_max_age_days: int
    ends_at_grace_minutes: int
    provider_timeout: int
    provider_max_workers: int
    state_retention_days: int


def _load_from_env() -> None:
    global LOG_LEVEL, LOG_FORMAT, LOG_DIR_PATH, LOG_MAX_BYTES, LOG_BACKUP_COUNT
    global OUT_PATH, FEED_HEALTH_PATH, FEED_HEALTH_JSON_PATH, FEED_TITLE, FEED_LINK, FEED_DESC, FEED_TTL
    global DESCRIPTION_CHAR_LIMIT, FRESH_PUBDATE_WINDOW_MIN, MAX_ITEMS
    global MAX_ITEM_AGE_DAYS, ABSOLUTE_MAX_AGE_DAYS, ENDS_AT_GRACE_MINUTES
    global PROVIDER_TIMEOUT, PROVIDER_MAX_WORKERS, STATE_FILE, STATE_RETENTION_DAYS
    global CACHE_MAX_AGE_HOURS

    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    LOG_FORMAT = os.getenv("LOG_FORMAT", "plain").strip().lower()
    LOG_DIR_PATH = resolve_env_path("LOG_DIR", Path("log"), allow_fallback=True)
    LOG_MAX_BYTES = max(get_int_env("LOG_MAX_BYTES", 1_000_000), 0)
    LOG_BACKUP_COUNT = max(get_int_env("LOG_BACKUP_COUNT", 5), 0)

    OUT_PATH = resolve_env_path("OUT_PATH", DEFAULT_OUT_PATH)
    FEED_HEALTH_PATH = resolve_env_path(
        "FEED_HEALTH_PATH", DEFAULT_FEED_HEALTH_PATH, allow_fallback=True
    )
    FEED_HEALTH_JSON_PATH = resolve_env_path(
        "FEED_HEALTH_JSON_PATH", DEFAULT_FEED_HEALTH_JSON_PATH, allow_fallback=True
    )
    FEED_TITLE = os.getenv("FEED_TITLE", DEFAULT_FEED_TITLE)
    FEED_LINK = os.getenv("FEED_LINK", DEFAULT_FEED_LINK)
    FEED_DESC = os.getenv("FEED_DESC", DEFAULT_FEED_DESCRIPTION)
    FEED_TTL = max(get_int_env("FEED_TTL", DEFAULT_FEED_TTL_MINUTES), 0)
    DESCRIPTION_CHAR_LIMIT = max(
        get_int_env("DESCRIPTION_CHAR_LIMIT", DEFAULT_DESCRIPTION_CHAR_LIMIT), 0
    )
    FRESH_PUBDATE_WINDOW_MIN = get_int_env(
        "FRESH_PUBDATE_WINDOW_MIN", DEFAULT_FRESH_PUBDATE_WINDOW_MIN
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
    PROVIDER_TIMEOUT = max(get_int_env("PROVIDER_TIMEOUT", DEFAULT_PROVIDER_TIMEOUT), 0)
    PROVIDER_MAX_WORKERS = max(
        get_int_env("PROVIDER_MAX_WORKERS", DEFAULT_PROVIDER_MAX_WORKERS), 0
    )
    STATE_FILE = resolve_env_path("STATE_PATH", DEFAULT_STATE_PATH)
    STATE_RETENTION_DAYS = max(
        get_int_env("STATE_RETENTION_DAYS", DEFAULT_STATE_RETENTION_DAYS), 0
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
    "OUT_PATH",
    "PROVIDER_MAX_WORKERS",
    "PROVIDER_TIMEOUT",
    "RFC",
    "STATE_FILE",
    "STATE_RETENTION_DAYS",
    "build_paths",
    "build_settings",
    "get_bool_env",
    "get_int_env",
    "refresh_from_env",
    "resolve_env_path",
    "validate_path",
]
