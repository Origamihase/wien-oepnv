"""Utility functions for reading and writing provider caches."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Callable, List, Optional

from .env import get_bool_env

_CACHE_DIR = Path("cache")
_CACHE_FILENAME = "events.json"

log = logging.getLogger(__name__)


_CacheAlertHook = Callable[[str, str], None]
_CACHE_ALERT_HOOKS: List[_CacheAlertHook] = []
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
    return _CACHE_DIR / provider / _CACHE_FILENAME


def cache_modified_at(provider: str) -> Optional[datetime]:
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
        log.warning(
            "Could not read mtime for cache '%s' at %s: %s",
            provider,
            cache_file,
            exc,
        )
        return None

    return datetime.fromtimestamp(stat_result.st_mtime, tz=timezone.utc)


def read_cache(provider: str) -> List[Any]:
    """Return cached events for *provider*.

    If the cache is missing or cannot be read, an empty list is returned and a
    warning is logged.
    """

    cache_file = _cache_file(provider)

    try:
        with cache_file.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except FileNotFoundError:
        log.warning("Cache for provider '%s' not found at %s", provider, cache_file)
        _emit_cache_alert(provider, f"Cache-Datei fehlt ({cache_file})")
    except json.JSONDecodeError as exc:
        log.warning(
            "Cache for provider '%s' at %s contains invalid JSON: %s",
            provider,
            cache_file,
            exc,
        )
        _emit_cache_alert(provider, f"Ungültiges JSON ({exc})")
    except OSError as exc:
        log.warning(
            "Could not read cache for provider '%s' at %s: %s",
            provider,
            cache_file,
            exc,
        )
        _emit_cache_alert(provider, f"Leseproblem ({exc})")
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


def _pretty_print_enabled(explicit: Optional[bool]) -> bool:
    """Return whether cache files should be pretty printed."""

    if explicit is not None:
        return explicit
    return get_bool_env("WIEN_OEPNV_CACHE_PRETTY", True)


def write_cache(provider: str, items: List[Any], *, pretty: Optional[bool] = None) -> None:
    """Write *items* to the cache for *provider* atomically.

    Pretty printing is enabled by default to keep JSON files human readable. To
    reduce cache size for large datasets set ``pretty`` to ``False`` or define
    the environment variable ``WIEN_OEPNV_CACHE_PRETTY=0``.
    """

    cache_file = _cache_file(provider)
    cache_file.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        dir=str(cache_file.parent),
        prefix="events.",
        suffix=".json.tmp",
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            try:
                pretty_print = _pretty_print_enabled(pretty)
                separators: tuple[str, str] | None = None
                indent: int | None = 2
                if not pretty_print:
                    indent = None
                    separators = (",", ":")

                json.dump(
                    items,
                    fh,
                    ensure_ascii=False,
                    indent=indent,
                    separators=separators,
                )
                fh.flush()
                os.fsync(fh.fileno())
            except Exception:
                log.exception(
                    "Failed to write cache for provider '%s' to temporary file %s",
                    provider,
                    tmp_path,
                )
                raise
        try:
            os.replace(tmp_path, cache_file)
        except OSError:
            log.exception(
                "Failed to replace cache for provider '%s' at %s with temporary file %s",
                provider,
                cache_file,
                tmp_path,
            )
            raise
    finally:
        # If anything went wrong before os.replace, ensure the temporary file is removed.
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
