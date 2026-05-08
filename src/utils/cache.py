"""Utility functions for reading and writing provider caches."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, UTC
from pathlib import Path
from threading import RLock
from typing import Any
from collections.abc import Callable

from .env import get_bool_env
from .files import atomic_write, safe_path_join, sanitize_filename
from .logging import sanitize_log_arg

_CACHE_DIR = Path("cache")
_CACHE_FILENAME = "events.json"
_STATUS_FILENAME = "last_run.json"

log = logging.getLogger(__name__)

# Security: ``MAX_PRUNE_CACHE_MAX_AGE_HOURS`` is the eviction-window ceiling for
# the on-disk cache pruner. ``prune_cache`` consumes ``max_age_hours`` as
# ``cutoff = now - timedelta(hours=max_age_hours)`` (direct ``datetime - timedelta``
# arithmetic). The default caller in ``write_cache`` uses the hardcoded 48-hour
# default, but the function is exported as a public API and a future caller
# passing an env-controlled or user-controlled value (e.g. a hypothetical
# ``CACHE_PRUNE_MAX_AGE_HOURS`` env var) would otherwise inherit the unbounded
# shape — a benign-looking value such as ``max_age_hours=999999999999`` raises
# ``OverflowError: Python int too large to convert to C int`` from the
# ``timedelta`` constructor (the C-level normalisation packs days into a signed
# 32-bit int, ~10**11 hours overflows that bound), and even at non-overflow
# values around ~17M hours the subsequent ``now - timedelta(hours=N)``
# subtraction underflows past Python's year-1 datetime boundary and raises
# ``OverflowError: date value out of range``. Both errors propagate out of
# ``prune_cache`` past the surrounding ``OSError`` handlers and crash the
# ``write_cache`` callers that wrap it. At non-overflow but unreasonably large
# values (e.g. 10000 hours ≈ 14 months) the pruner never evicts anything, the
# ``cache/`` directory grows unboundedly, and the repo-bloat purpose of the
# function is silently defeated. Capping inside the function (defense-in-depth)
# means every caller — current and future — inherits the ceiling without having
# to remember to add it. 8760 hours (1 year) is generous (~182x default) and
# bounds ``now - timedelta(hours=N)`` safely within Python's datetime range.
# TIGHTEN-only contract mirrors ``MAX_LOG_PRUNE_KEEP_DAYS`` in
# ``src/feed/logging.py`` and ``MAX_CACHE_MAX_AGE_HOURS`` in
# ``src/feed/config.py`` — same env-cap drift family (env-derived integer
# feeding ``timedelta(unit=N)`` into ``datetime - timedelta`` arithmetic).
MAX_PRUNE_CACHE_MAX_AGE_HOURS = 8760

# Security: defense-in-depth cap on the byte size of any on-disk cache file
# that the loaders below feed to ``json.load``. The depth-bomb defence
# (``except RecursionError`` from the 2026-05-08 round) covers the *deeply-
# nested* attack shape, but ``json.load`` does NOT raise ``RecursionError``
# on a wide-but-flat document such as ``[1, 1, … (50 million times) … 1]``
# — the parser allocates one Python ``int`` (~28 bytes) per element plus
# list overhead (~8 bytes per slot), so a 50 MiB on-disk file balloons to
# ~500 MiB resident memory and a multi-GiB file pushes past the cron
# runner's ulimit / cgroup memory cap and crashes via ``MemoryError``.
# ``MemoryError`` is a ``BaseException`` — it is NOT caught by the
# surrounding ``except (json.JSONDecodeError, OSError, RecursionError)``
# handlers in ``read_cache`` / ``read_status`` / ``write_cache``'s
# degradation guard, so the unhandled exception escapes the feed
# orchestrator's main ``try`` block and crashes the whole cron build.
# Threat model: a compromised CI runner, a partial flush after power
# loss, or a corrupted previous run plants a multi-MiB-to-multi-GiB
# file under ``cache/<provider>/``. 50 MiB is ~100x the largest
# legitimate cache observed in production and bounds the worst-case
# parse cost at <500 MiB resident memory which fits inside the cron
# runner's standard 1 GiB cgroup limit.
MAX_CACHE_FILE_BYTES = 50 * 1024 * 1024


class DataDegradationError(Exception):
    """Raised when an operation would severely degrade data quality."""
    pass


_CacheAlertHook = Callable[[str, str], None]
_CACHE_ALERT_HOOKS: list[_CacheAlertHook] = []
_CACHE_ALERT_LOCK = RLock()


def register_cache_alert_hook(callback: _CacheAlertHook) -> Callable[[], None]:
    """Register ``callback`` to receive cache alert notifications.

    The callback is invoked with ``(provider, message)`` whenever :func:`read_cache`
    encounters an issue (missing files, invalid JSON, etc.).  A callable is
    returned that removes the hook again.  Callers should ensure the unregister
    function is executed (e.g. via ``try``/``finally``) to avoid leaking hooks
    across runs.
    """

    with _CACHE_ALERT_LOCK:
        _CACHE_ALERT_HOOKS.append(callback)

    def _unregister() -> None:
        with _CACHE_ALERT_LOCK:
            try:
                _CACHE_ALERT_HOOKS.remove(callback)
            except ValueError:
                pass

    return _unregister


def _emit_cache_alert(provider: str, message: str) -> None:
    if not provider or not message:
        return
    with _CACHE_ALERT_LOCK:
        hooks = list(_CACHE_ALERT_HOOKS)

    for hook in hooks:
        try:
            hook(provider, message)
        except Exception:  # pragma: no cover - defensive guard for user hooks
            log.exception("Cache alert hook failed for provider '%s'", provider)


def _cache_file(provider: str) -> Path:
    if not re.match(r"^[a-zA-Z0-9_-]+$", provider):
        raise ValueError(f"Invalid cache key format: {provider}")
    return safe_path_join(_CACHE_DIR, sanitize_filename(provider), _CACHE_FILENAME)


def _status_file(provider: str) -> Path:
    if not re.match(r"^[a-zA-Z0-9_-]+$", provider):
        raise ValueError(f"Invalid cache key format: {provider}")
    return safe_path_join(_CACHE_DIR, sanitize_filename(provider), _STATUS_FILENAME)


def cache_modified_at(provider: str) -> datetime | None:
    """Return the last modification timestamp for ``provider``'s cache.

    ``None`` is returned if the cache file does not exist or cannot be read.
    The timestamp is always normalised to UTC to simplify comparisons.
    """

    cache_file = _cache_file(provider)
    try:
        stat_result = cache_file.stat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        # Security (Clear-Text-Logging Drift, src/utils/* round): route the
        # bound exception text through ``sanitize_log_arg`` so a hostile
        # ``__str__`` (custom subclass / third-party adapter / planted
        # filename carrying control bytes via OSError.filename) cannot
        # smuggle ANSI / BiDi / log-forging payloads into operator logs.
        log.warning(
            "Could not read mtime for cache '%s' at %s: %s",
            provider,
            cache_file,
            sanitize_log_arg(str(exc)),
        )
        return None

    mtime = datetime.fromtimestamp(stat_result.st_mtime, tz=UTC)
    # Reject cache if it is more than 24 hours in the future
    if mtime > datetime.now(UTC) + timedelta(hours=24):
        log.warning(
            "Cache for provider '%s' at %s is suspiciously far in the future (%s). Treating as missing.",
            provider, cache_file, mtime
        )
        return None
    return mtime


def read_cache(provider: str) -> list[Any]:
    """Return cached events for *provider*.

    If the cache is missing or cannot be read, an empty list is returned and a
    warning is logged.
    """

    cache_file = _cache_file(provider)

    try:
        # Security: open first, then ``os.fstat`` the file descriptor —
        # closes the TOCTOU window between ``Path.stat`` and ``Path.open``
        # that lets a parallel writer / symlink swap bypass the cap. The
        # defensive ``read(MAX_CACHE_FILE_BYTES + 1)`` defends against
        # special files (FIFOs, ``/dev/zero``) that report ``st_size == 0``
        # but yield unbounded bytes on read. See ``MAX_CACHE_FILE_BYTES``
        # for the planted-huge-file threat model.
        with cache_file.open("rb") as fh:
            if os.fstat(fh.fileno()).st_size > MAX_CACHE_FILE_BYTES:
                log.warning(
                    "Cache für Provider '%s' bei %s ist zu groß (> %d Bytes); überspringe.",
                    provider, cache_file, MAX_CACHE_FILE_BYTES,
                )
                _emit_cache_alert(
                    provider,
                    f"Cache-Datei zu groß (> {MAX_CACHE_FILE_BYTES} Bytes)",
                )
                return []
            raw = fh.read(MAX_CACHE_FILE_BYTES + 1)
            if len(raw) > MAX_CACHE_FILE_BYTES:
                log.warning(
                    "Cache für Provider '%s' bei %s überschreitet %d Bytes beim Lesen; überspringe.",
                    provider, cache_file, MAX_CACHE_FILE_BYTES,
                )
                _emit_cache_alert(
                    provider,
                    f"Cache-Datei zu groß (> {MAX_CACHE_FILE_BYTES} Bytes)",
                )
                return []
            payload = json.loads(raw)
    except FileNotFoundError:
        log.warning("Cache for provider '%s' not found at %s", provider, cache_file)
        _emit_cache_alert(provider, f"Cache-Datei fehlt ({cache_file})")
    except (json.JSONDecodeError, RecursionError, UnicodeDecodeError) as exc:
        # Security: ``RecursionError`` covers JSON depth-bomb attacks via a
        # poisoned cache file (left by a corrupted previous run, planted by
        # a compromised CI runner, or written during a partial flush
        # followed by power loss). ``json.load`` raises ``RecursionError``
        # (NOT a subclass of ``json.JSONDecodeError``) on a deeply-nested
        # but well-formed payload — without this catch the unhandled error
        # propagates out of the orchestrator's main ``try`` block and
        # crashes the entire feed build.
        # Security (Clear-Text-Logging Drift): the bound ``exc`` text is
        # forwarded both into the WARNING log AND into the cache-alert
        # callback chain (Slack / PagerDuty / feed-health.json renders).
        # Sanitise once at the boundary so neither channel can carry
        # ANSI / BiDi / log-forging payloads from a hostile ``__str__``.
        sanitized_exc = sanitize_log_arg(str(exc))
        log.warning(
            "Cache for provider '%s' at %s contains invalid JSON: %s",
            provider,
            cache_file,
            sanitized_exc,
        )
        _emit_cache_alert(provider, f"Ungültiges JSON ({sanitized_exc})")
    except OSError as exc:
        sanitized_exc = sanitize_log_arg(str(exc))
        log.warning(
            "Could not read cache for provider '%s' at %s: %s",
            provider,
            cache_file,
            sanitized_exc,
        )
        _emit_cache_alert(provider, f"Leseproblem ({sanitized_exc})")
    else:
        if isinstance(payload, list):
            return payload
        log.warning(
            "Cache for provider '%s' at %s does not contain a JSON array (found %s)",
            provider,
            cache_file,
            type(payload).__name__,
        )
        _emit_cache_alert(provider, "Cache-Inhalt ist keine Liste")

    return []


def prune_cache(max_age_hours: int = 48) -> None:
    """Evict cached files older than `max_age_hours` hours to prevent repo bloat.

    Iterates through the cache directory and deletes `events.json` files that
    are older than the specified age. Removes the provider directory if empty.
    """
    if max_age_hours <= 0:
        return
    # Security: clamp ``max_age_hours`` to ``MAX_PRUNE_CACHE_MAX_AGE_HOURS`` to
    # defeat the ``timedelta`` constructor / ``datetime - timedelta`` underflow
    # vector documented at the constant declaration above. Without the cap a
    # caller passing ``max_age_hours=99999999`` would crash the cron job via
    # OverflowError.
    if max_age_hours > MAX_PRUNE_CACHE_MAX_AGE_HOURS:
        max_age_hours = MAX_PRUNE_CACHE_MAX_AGE_HOURS
    if not _CACHE_DIR.is_dir():
        return

    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=max_age_hours)

    for provider_dir in _CACHE_DIR.iterdir():
        if not provider_dir.is_dir():
            continue

        cache_file = provider_dir / _CACHE_FILENAME
        if cache_file.exists():
            try:
                mtime = datetime.fromtimestamp(cache_file.stat().st_mtime, tz=UTC)
                if mtime < cutoff:
                    cache_file.unlink()
                    log.info("Evicted old cache file: %s", cache_file)
            except OSError as exc:
                # Security (Clear-Text-Logging Drift): see the read_cache
                # ``except OSError`` branch above for the full threat model.
                log.warning(
                    "Failed to check or delete old cache file %s: %s",
                    cache_file,
                    sanitize_log_arg(str(exc)),
                )

        # Remove the directory if it's now empty
        try:
            if not any(provider_dir.iterdir()):
                provider_dir.rmdir()
                log.info("Removed empty provider directory: %s", provider_dir)
        except OSError:
            pass


def _pretty_print_enabled(explicit: bool | None) -> bool:
    """Return whether cache files should be pretty printed."""

    if explicit is not None:
        return explicit
    return get_bool_env("WIEN_OEPNV_CACHE_PRETTY", True)


def _stable_sort_key(item: Any) -> tuple[str, str, str, str]:
    """Stabiler Sortierschlüssel für Cache-Items.

    Verwendet ``_identity`` als primäres Kriterium (vom Provider explizit gesetzt
    und bewusst gegen Titel-Kosmetik invariant), dann ``guid``, danach ``title``
    und ``source`` als Tie-Breaker für Items, denen die Hauptfelder fehlen. Items,
    die keine Dicts sind, sortieren konsistent auf den leeren Tupel.
    """
    if not isinstance(item, dict):
        return ("", "", "", "")
    return (
        str(item.get("_identity") or ""),
        str(item.get("guid") or ""),
        str(item.get("title") or ""),
        str(item.get("source") or ""),
    )


def write_cache(provider: str, items: list[Any], *, pretty: bool | None = None) -> None:
    """Write *items* to the cache for *provider* atomically.

    Pretty printing is enabled by default to keep JSON files human readable. To
    reduce cache size for large datasets set ``pretty`` to ``False`` or define
    the environment variable ``WIEN_OEPNV_CACHE_PRETTY=0``.
    """

    prune_cache()

    cache_file = _cache_file(provider)

    # Data Degradation Guard
    if cache_file.exists():
        try:
            # Security: open-then-fstat closes the TOCTOU between the cap
            # check and ``open()`` — a parallel writer's atomic_write
            # rename could otherwise swap the inode between the two
            # syscalls. ``read(MAX_CACHE_FILE_BYTES + 1)`` defends against
            # zero-st_size special files. An oversized existing cache is
            # treated as unreadable/corrupt so the new payload overwrites
            # without consulting the planted state.
            with cache_file.open("rb") as fh:
                if os.fstat(fh.fileno()).st_size <= MAX_CACHE_FILE_BYTES:
                    raw = fh.read(MAX_CACHE_FILE_BYTES + 1)
                    if len(raw) <= MAX_CACHE_FILE_BYTES:
                        existing_data = json.loads(raw)
                        if isinstance(existing_data, list) and len(existing_data) > 0:
                            if len(items) == 0:
                                raise DataDegradationError(
                                    f"Empty payload rejected: refusing to overwrite cache for '{provider}' "
                                    f"which currently has {len(existing_data)} items."
                                )
                            if len(items) < len(existing_data) * 0.2:
                                raise DataDegradationError(
                                    f"Degraded payload rejected: '{provider}' items dropped drastically "
                                    f"from {len(existing_data)} to {len(items)}."
                                )
        except (json.JSONDecodeError, OSError, RecursionError, UnicodeDecodeError):
            # Security: ``RecursionError`` covers JSON depth-bomb attacks
            # in the EXISTING on-disk cache (planted by a compromised
            # runner / corrupted previous run). Without the catch the
            # data-degradation guard would crash the cron mid-write
            # instead of treating the unparseable cache as overwriteable.
            # Ignore read errors to allow corrupt caches to be overwritten.
            pass

    # atomic_write creates parents if needed

    try:
        # Explicitly set 0600 permissions for defense in depth
        with atomic_write(
            cache_file, mode="w", encoding="utf-8", permissions=0o600
        ) as fh:
            pretty_print = _pretty_print_enabled(pretty)
            separators: tuple[str, str] | None = None
            indent: int | None = 2
            if not pretty_print:
                indent = None
                separators = (",", ":")

            # Deterministische Sortierung gegen Diff-Reshuffle bei jedem Cache-Update.
            # Reduziert die History-Bloat erheblich, ohne dass sich Inhalt oder
            # Reihenfolge im Feed ändern (Items werden im Builder ohnehin neu sortiert).
            sorted_items = sorted(items, key=_stable_sort_key)

            json.dump(
                sorted_items,
                fh,
                ensure_ascii=False,
                indent=indent,
                separators=separators,
            )
    except Exception:
        log.exception(
            "Failed to write cache for provider '%s' to %s",
            provider,
            cache_file,
        )
        raise


def write_status(provider: str, status: dict[str, Any]) -> None:
    """Persist a heartbeat record for ``provider`` next to its events cache.

    The status file lives at ``cache/<sanitized provider>/last_run.json`` and
    is intended to make workflow runs visible in git even when the events
    payload is unchanged (e.g. an empty provider response collapsing into the
    same ``[]`` cache file commit after commit).
    """

    if not isinstance(status, dict):
        raise TypeError("status must be a dict")

    status_file = _status_file(provider)

    try:
        with atomic_write(
            status_file, mode="w", encoding="utf-8", permissions=0o600
        ) as fh:
            json.dump(status, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
    except Exception:
        log.exception(
            "Failed to write status for provider '%s' to %s",
            provider,
            status_file,
        )
        raise


def read_status(provider: str) -> dict[str, Any] | None:
    """Return the persisted heartbeat for ``provider`` or ``None``."""

    status_file = _status_file(provider)
    try:
        # Security: open-then-fstat closes the TOCTOU between cap check
        # and ``open()`` — see ``read_cache`` above for the full TOCTOU
        # threat model. ``read(MAX_CACHE_FILE_BYTES + 1)`` defends against
        # zero-st_size special files (FIFOs, ``/dev/zero``).
        with status_file.open("rb") as fh:
            if os.fstat(fh.fileno()).st_size > MAX_CACHE_FILE_BYTES:
                log.warning(
                    "Status für Provider '%s' bei %s ist zu groß (> %d Bytes); überspringe.",
                    provider, status_file, MAX_CACHE_FILE_BYTES,
                )
                return None
            raw = fh.read(MAX_CACHE_FILE_BYTES + 1)
            if len(raw) > MAX_CACHE_FILE_BYTES:
                log.warning(
                    "Status für Provider '%s' bei %s überschreitet %d Bytes beim Lesen; überspringe.",
                    provider, status_file, MAX_CACHE_FILE_BYTES,
                )
                return None
            payload = json.loads(raw)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError, RecursionError, UnicodeDecodeError) as exc:
        # Security: ``RecursionError`` covers JSON depth-bomb attacks in
        # the on-disk status file. See ``read_cache`` above for the full
        # threat model — same canonical defence pattern applied here so
        # a poisoned ``last_run.json`` does not crash heartbeat reads.
        # Security (Clear-Text-Logging Drift): see the read_cache branch.
        log.warning(
            "Could not read status for provider '%s' at %s: %s",
            provider,
            status_file,
            sanitize_log_arg(str(exc)),
        )
        return None

    if not isinstance(payload, dict):
        return None
    return payload
