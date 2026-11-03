"""The provider registry, the cache readers and the provider tuning.

This module owns ``PROVIDERS``, ``DEFAULT_PROVIDERS`` and
``_PROVIDERS_INITIALIZED`` with every ``global`` statement on them, and
it runs ``load_provider_plugins()`` once when it is imported, as
importing build_feed did. ``src.build_feed`` re-exports the same
``PROVIDERS`` list, but not ``_PROVIDERS_INITIALIZED``: tests rebind
these names here, so they read and patch them in this module.

Moved verbatim from ``src/build_feed.py`` (700-line rule, Etappe 8); ``src.build_feed``
re-exports every name that src/, scripts/ or the tests read from it.
"""

from __future__ import annotations

import inspect
import os
import re
from datetime import datetime, timedelta, UTC
from typing import Any, cast, NamedTuple

from ..feed import config as feed_config
from ..feed.providers import (
    iter_providers, load_provider_plugins, provider_statuses, register_provider, resolve_provider_name,
)
from ..feed.reporting import RunReport
from ..utils.cache import cache_modified_at, read_cache as _core_read_cache
from ..utils.logging import sanitize_log_arg

from .base import log
from .cache_filters import _post_filter_baustellen, _post_filter_oebb, _post_filter_wl

__all__ = [
    "DEFAULT_PROVIDERS", "PROVIDERS", "_PROVIDERS_INITIALIZED", "reset_module_state", "init_providers",
    "_provider_display_name", "_detect_stale_caches", "_provider_statuses", "_log_startup_summary",
    "_validate_configuration", "_read_optional_non_negative_int", "_provider_timeout_override",
    "_provider_concurrency_key", "_provider_worker_limit", "_fetch_supports_timeout", "_call_fetch_with_timeout",
    "_categorize_providers",
]


read_cache = _core_read_cache


def read_cache_wl() -> list[Any]:
    return _post_filter_wl(list(read_cache("wl") or []))


def read_cache_oebb() -> list[Any]:
    return _post_filter_oebb(list(read_cache("oebb") or []))


def read_cache_baustellen() -> list[Any]:
    return _post_filter_baustellen(list(read_cache("baustellen") or []))


# Stammstrecke feed events are now derived from the CSV ledger
# ``data/stats/stammstrecke_<YYYY>.csv`` rather than a JSON cache —
# see :mod:`src.feed.stammstrecke` for the operational contract
# (1-hour feed window, 6-hour episode lookback, 9-minute threshold).
# Replaced the JSON cache 2026-05-09 (PR follow-up to #1397) so the
# README dashboard and the RSS feed share a single source of truth.
def read_cache_stammstrecke() -> list[Any]:
    """Compute Stammstrecke feed events from the CSV ledger.

    Delegates to :func:`src.feed.stammstrecke.compute_stammstrecke_events`.
    A missing / unreadable / empty ledger naturally yields zero
    events; the feed build then omits the Stammstrecke entry rather
    than failing.
    """

    from ..feed import stammstrecke as stammstrecke_events

    return cast(list[Any], stammstrecke_events.compute_stammstrecke_events())


DEFAULT_PROVIDERS: tuple[tuple[str, Any], ...] = (
    ("WL_ENABLE", read_cache_wl),
    ("OEBB_ENABLE", read_cache_oebb),
    ("BAUSTELLEN_ENABLE", read_cache_baustellen),
    # VOR is intentionally absent — VOR API access is scoped to the
    # S-Bahn-Stammstrecke delay monitor only (operator policy
    # 2026-05-11). Stammstrecke feed entries derive from the CSV
    # ledger via :mod:`src.feed.stammstrecke`, not from a VOR
    # disruption cache.
    ("STAMMSTRECKE_ENABLE", read_cache_stammstrecke),
)

PROVIDERS: list[tuple[str, Any]] = list(DEFAULT_PROVIDERS)

_PROVIDERS_INITIALIZED = False

# Ensure plugins are loaded exactly once per process startup AFTER env vars are configured
# and BEFORE the main provider registration loop.
load_provider_plugins()

def reset_module_state() -> None:
    """Test helper to reset the module-level initialization state."""
    global _PROVIDERS_INITIALIZED
    global PROVIDERS
    _PROVIDERS_INITIALIZED = False
    PROVIDERS.clear()
    PROVIDERS.extend(DEFAULT_PROVIDERS)

def init_providers() -> None:
    global _PROVIDERS_INITIALIZED
    if _PROVIDERS_INITIALIZED:
        return
    for env_name, loader in PROVIDERS:
        register_provider(env_name, loader, cache_key=resolve_provider_name(loader, env_name))
    _PROVIDERS_INITIALIZED = True


def _provider_display_name(fetch: Any, env: str | None = None) -> str:
    """Resolve the display name for a provider based on its loader or env var."""
    return resolve_provider_name(fetch, env)


def _detect_stale_caches(report: RunReport, now: datetime) -> list[str]:
    """Record warnings for provider caches older than the configured threshold."""

    if feed_config.CACHE_MAX_AGE_HOURS <= 0:
        return []

    threshold = timedelta(hours=feed_config.CACHE_MAX_AGE_HOURS)
    stale_messages: list[str] = []

    for spec in iter_providers():
        loader = spec.loader
        cache_name = getattr(loader, "_provider_cache_name", None)
        if not cache_name:
            continue

        modified_at = cache_modified_at(str(cache_name))
        if modified_at is None:
            continue

        if modified_at.tzinfo is None:
            modified_at = modified_at.replace(tzinfo=UTC)

        age = now - modified_at
        if age <= threshold:
            continue

        hours = age.total_seconds() / 3600
        message = (
            f"Cache {cache_name}: zuletzt vor {hours:.1f}h aktualisiert "
            f"(Schwelle {feed_config.CACHE_MAX_AGE_HOURS}h)"
        )
        report.add_warning(message)
        stale_messages.append(message)

    return stale_messages


def _provider_statuses() -> list[tuple[str, bool]]:
    """Return a list of (name, enabled) tuples for all registered providers."""
    return provider_statuses()


def _log_startup_summary(statuses: list[tuple[str, bool]]) -> None:
    """Log the active configuration and enabled providers at startup."""
    enabled = sorted(name for name, is_enabled in statuses if is_enabled)
    disabled = sorted(name for name, is_enabled in statuses if not is_enabled)

    enabled_display = ", ".join(enabled) if enabled else "keine"
    log.info(
        "Starte Feed-Bau: %s aktiv (Timeout global=%ss, MaxItems=%d, Worker=%s)",
        enabled_display,
        feed_config.PROVIDER_TIMEOUT,
        feed_config.MAX_ITEMS,
        feed_config.PROVIDER_MAX_WORKERS or "auto",
    )
    if disabled:
        log.info("Deaktivierte Provider: %s", ", ".join(disabled))


def _validate_configuration(statuses: list[tuple[str, bool]]) -> None:
    """Check the runtime configuration for common issues (e.g. no providers active)."""
    enabled_count = sum(1 for _, is_enabled in statuses if is_enabled)
    if not statuses:
        log.warning("Keine Provider registriert – es werden keine Items gesammelt.")
    elif enabled_count == 0:
        log.error(
            "Alle Provider deaktiviert – Feed bleibt leer, bitte Konfiguration prüfen."
        )

    if feed_config.MAX_ITEMS == 0:
        log.warning("MAX_ITEMS ist 0 – der Feed wird ohne Einträge erzeugt.")
    if feed_config.FEED_TTL == 0:
        log.warning(
            "FEED_TTL ist 0 – Clients werten den Feed unmittelbar als abgelaufen."
        )
    if feed_config.PROVIDER_TIMEOUT == 0 and enabled_count:
        log.warning(
            "PROVIDER_TIMEOUT ist 0 – Netzwerkprovider haben keine Zeit für Antworten."
        )
    if feed_config.MAX_ITEM_AGE_DAYS > feed_config.ABSOLUTE_MAX_AGE_DAYS:
        log.warning(
            "MAX_ITEM_AGE_DAYS (%s) übersteigt ABSOLUTE_MAX_AGE_DAYS (%s) – ältere Items "
            "werden dennoch durch den absoluten Grenzwert verworfen.",
            feed_config.MAX_ITEM_AGE_DAYS,
            feed_config.ABSOLUTE_MAX_AGE_DAYS,
        )

# ---------------- Provider tuning ----------------

def _provider_env_slug(name: str) -> str:
    """Convert a provider name into a slug suitable for environment variables."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", (name or "").upper()).strip("_")
    return slug or "PROVIDER"


def _read_optional_non_negative_int(env_name: str) -> int | None:
    """Read a non-negative integer from an environment variable."""
    raw = os.getenv(env_name)
    if raw is None:
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    try:
        value = int(stripped)
    except (TypeError, ValueError) as exc:
        # Security (Clear-Text-Logging Drift, src/utils/* round): the bound
        # ``exc`` text is rendered raw via ``%s`` — sanitise to defeat a
        # hostile env-injected value flowing through ``int()``'s error
        # message into the structured log line.
        #
        # Security (Path-Log Sibling Drift Round 4, env-repr closure):
        # ``raw`` was previously formatted via ``%r``; that conversion
        # leaves all 256 Variation Selectors (U+FE00-U+FE0F +
        # U+E0100-U+E01EF) in ``record.args`` and ``getMessage()``
        # verbatim. Route through ``sanitize_log_arg`` to strip the
        # canonical ``_INVISIBLE_DANGEROUS_RE`` union BEFORE the value
        # reaches caplog / non-SafeFormatter handlers.
        log.warning(
            "Ungültiger Wert für %s=%s – ignoriere Override (%s: %s)",
            env_name,
            sanitize_log_arg(raw),
            type(exc).__name__,
            sanitize_log_arg(str(exc)),
        )
        return None
    if value < 0:
        # Security (Path-Log Sibling Drift Round 4, env-repr closure):
        # ``raw`` is the operator-controlled per-provider env override
        # (``PROVIDER_<SLUG>_MAX_AGE_DAYS`` etc.). Pre-fix the WARNING
        # interpolated it via ``%r`` — Python's repr() escapes most
        # attack bytes but lets all 256 Variation Selectors
        # (U+FE00-U+FE0F + U+E0100-U+E01EF) through verbatim into
        # ``record.args[1]`` and ``record.getMessage()``. Route through
        # ``sanitize_log_arg`` so the canonical
        # ``_INVISIBLE_DANGEROUS_RE`` strips them BEFORE the value
        # lands in caplog / non-SafeFormatter handlers (mirrors the
        # sibling defence on line 374 that already routes ``str(exc)``
        # through the canonical helper).
        log.warning(
            "Negativer Wert für %s=%s – ignoriere Override",
            env_name,
            sanitize_log_arg(raw),
        )
        return None
    return value


def _resolve_provider_override(candidates: list[str]) -> int | None:
    """Helper to resolve the first valid integer from a list of env var candidates."""
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        value = _read_optional_non_negative_int(candidate)
        if value is not None:
            return value
    return None


def _provider_timeout_override(
    fetch: Any, env: str | None, provider_name: str
) -> int | None:
    """Determine if a specific timeout override exists for this provider."""
    candidates: list[str] = []
    custom_env = getattr(fetch, "_provider_timeout_env", None)
    if isinstance(custom_env, str) and custom_env.strip():
        candidates.append(custom_env.strip())

    slug = _provider_env_slug(provider_name)
    candidates.append(f"PROVIDER_TIMEOUT_{slug}")

    if env:
        base = env.removesuffix("_ENABLE")
        candidates.append(f"{base}_TIMEOUT")
        candidates.append(f"PROVIDER_TIMEOUT_{base}")

    value = _resolve_provider_override(candidates)
    if value is None:
        return None
    # Security: clamp the per-provider override at the same Slowloris-defence
    # ceiling as the global PROVIDER_TIMEOUT. Without this cap an env override
    # such as PROVIDER_TIMEOUT_VOR=99999 would bypass feed_config.PROVIDER_TIMEOUT
    # (which is itself capped at MAX_PROVIDER_TIMEOUT) and let a sluggish or
    # attacker-controlled upstream peer stall a fetch for ~28 hours.
    return min(value, feed_config.MAX_PROVIDER_TIMEOUT)


def _provider_concurrency_key(fetch: Any, provider_name: str) -> str:
    """Return the key used to group providers for concurrency limits."""
    key = getattr(fetch, "_provider_concurrency_key", None)
    if isinstance(key, str) and key.strip():
        return key.strip()
    return provider_name


def _provider_worker_limit(
    fetch: Any, env: str | None, provider_name: str, concurrency_key: str
) -> int | None:
    """Determine the maximum number of concurrent workers for this provider group."""
    candidates: list[str] = []
    custom_env = getattr(fetch, "_provider_max_workers_env", None)
    if isinstance(custom_env, str) and custom_env.strip():
        candidates.append(custom_env.strip())

    slug = _provider_env_slug(concurrency_key)
    candidates.append(f"PROVIDER_MAX_WORKERS_{slug}")

    if env:
        base = env.removesuffix("_ENABLE")
        candidates.append(f"{base}_MAX_WORKERS")

    return _resolve_provider_override(candidates)


def _fetch_supports_timeout(fetch: Any) -> bool:
    """Check if the fetch callable accepts a 'timeout' argument."""
    try:
        signature = inspect.signature(fetch)
    except (TypeError, ValueError):
        return False
    for param in signature.parameters.values():
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            return True
        if param.name == "timeout":
            return True
    return False


def _call_fetch_with_timeout(
    fetch: Any, timeout: int | float | None, supports_timeout: bool
) -> Any:
    """Invoke the fetch callable, passing the timeout if supported.

    Trusts the :func:`_fetch_supports_timeout` introspection result —
    pre-fix the ``except TypeError: return fetch()`` retry caught any
    TypeError raised from INSIDE the fetch body (e.g. an ``int(timeout)``
    conversion failure, a type-assertion error), then re-invoked the
    fetcher with no kwargs. The retry duplicated HTTP requests, side
    effects, quota debits, and the ``report.provider_started`` event
    sequence — masking the real internal error behind a doubled run.
    """
    if supports_timeout:
        return fetch(timeout=None if timeout is None else timeout)
    return fetch()


# ---------------- Pipeline ----------------


class _ProviderBuckets(NamedTuple):
    cache_fetchers: list[Any]
    network_fetchers: list[Any]
    provider_names: dict[Any, str]
    provider_envs: dict[Any, str | None]


def _categorize_providers(report: RunReport) -> _ProviderBuckets:
    """Walk PROVIDERS + plugin entrypoints and split enabled fetchers into
    cache-backed (sync) and network-backed (async) buckets, registering each
    with the report so disabled providers still appear in the health output.
    """
    cache_fetchers: list[Any] = []
    network_fetchers: list[Any] = []
    provider_names: dict[Any, str] = {}
    provider_envs: dict[Any, str | None] = {}

    provider_entries = list(PROVIDERS)
    providers_overridden = list(PROVIDERS) != list(DEFAULT_PROVIDERS)
    if provider_entries:
        if not providers_overridden:
            known_envs = {env for env, _ in provider_entries}
            for spec in iter_providers():
                if spec.env_var not in known_envs:
                    provider_entries.append((spec.env_var, spec.loader))
    else:
        provider_entries = [(spec.env_var, spec.loader) for spec in iter_providers()]

    for env, fetch in provider_entries:
        provider_name = _provider_display_name(fetch, env)
        enabled = bool(feed_config.get_bool_env(env, True))
        fetch_type = "cache" if getattr(fetch, "_provider_cache_name", None) else "network"
        report.register_provider(provider_name, enabled, fetch_type)
        if not enabled:
            continue
        provider_names[fetch] = provider_name
        provider_envs[fetch] = env
        if getattr(fetch, "_provider_cache_name", None):
            cache_fetchers.append(fetch)
        else:
            network_fetchers.append(fetch)

    return _ProviderBuckets(cache_fetchers, network_fetchers, provider_names, provider_envs)
