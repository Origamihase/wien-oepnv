"""Utility functions for reading and writing provider caches."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import tempfile
from typing import Any, List, Optional

from .env import get_bool_env

_CACHE_DIR = Path("cache")
_CACHE_FILENAME = "events.json"

log = logging.getLogger(__name__)


def _cache_file(provider: str) -> Path:
    return _CACHE_DIR / provider / _CACHE_FILENAME


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
    except json.JSONDecodeError as exc:
        log.warning(
            "Cache for provider '%s' at %s contains invalid JSON: %s",
            provider,
            cache_file,
            exc,
        )
    except OSError as exc:
        log.warning(
            "Could not read cache for provider '%s' at %s: %s",
            provider,
            cache_file,
            exc,
        )
    else:
        if isinstance(payload, list):
            return payload
        log.warning(
            "Cache for provider '%s' at %s does not contain a JSON array (found %s)",
            provider,
            cache_file,
            type(payload).__name__,
        )

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
                separators = None
                indent = 2
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
