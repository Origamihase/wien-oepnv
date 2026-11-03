"""Paths, environment lookups and the daily budget of the VOR provider.

Split off ``src/providers/vor.py`` (700-line rule, 2026-11-02), which
re-exports these names. ``src.providers.vor`` and ``src.providers.vor_quota``
both read them; this module imports neither, so neither imports the other
back: ``vor_quota`` takes ``MAX_REQUESTS_PER_DAY``, ``_resolve_path`` and
``log`` from here, not from the facade.

``log`` is the facade's logger (``src.providers.vor``), so records keep their
logger name. ``_load_int_env`` and ``_resolve_path`` warn through
:func:`_log_warning`; the facade rebinds it to its own ``_log_warning`` as soon
as it defines it, which also masks the VOR credentials. Until then (the
computation of ``MAX_REQUESTS_PER_DAY`` below, while the facade is not loaded
yet or is being reloaded) the credentials are not loaded and the facade's
version would mask nothing either; the stand-in here logs the same record.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from ..utils.logging import sanitize_log_arg, sanitize_log_message

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"

log = logging.getLogger("src.providers.vor")


def _get_env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _log_warning(message: str, *args: Any) -> None:
    """``src.providers.vor._log_warning`` with no credentials loaded.

    The facade replaces this binding with its own function once defined.
    """
    sanitized_msg = sanitize_log_message(message, secrets=[])
    if args:
        sanitized_args = tuple(sanitize_log_arg(arg, secrets=[]) for arg in args)
        log.warning(sanitized_msg, *sanitized_args)
    else:
        log.warning("%s", sanitized_msg)


# "VAO Start" contract limit: 100 requests per day (hard limit).
DEFAULT_MAX_REQUESTS_PER_DAY = 100


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


__all__ = [
    "BASE_DIR",
    "DATA_DIR",
    "DEFAULT_MAX_REQUESTS_PER_DAY",
    "MAX_REQUESTS_PER_DAY",
    "_get_env",
    "_load_int_env",
    "_log_warning",
    "_resolve_path",
    "log",
]
