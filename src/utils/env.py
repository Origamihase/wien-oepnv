#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Helpers for reading environment variables in a safe way.

The module now also provides lightweight helpers to populate environment
variables from ``.env`` style files.  This allows local development setups to
store API credentials alongside the repository without committing them to
version control.  Consumers can call :func:`load_default_env_files` before
importing provider modules to ensure secrets such as ``VOR_ACCESS_ID`` are
available.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Dict, Iterable, Mapping, MutableMapping

__all__ = [
    "get_int_env",
    "get_bool_env",
    "load_env_file",
    "load_default_env_files",
]

_TRUE_VALUES = {"1", "true", "t", "yes", "y", "on"}
_FALSE_VALUES = {"0", "false", "f", "no", "n", "off"}


def get_bool_env(name: str, default: bool) -> bool:
    """Read boolean environment variables safely.

    Supported truthy values are ``1``, ``true``, ``t``, ``yes``, ``y`` and
    ``on`` (case-insensitive).  Falsy values are ``0``, ``false``, ``f``,
    ``no``, ``n`` and ``off``.  Unset variables or values consisting solely of
    whitespace result in the provided default.  All other values trigger a
    warning and also fall back to the default.
    """

    raw = os.getenv(name)
    if raw is None:
        return default

    stripped = raw.strip()
    if not stripped:
        return default

    lowered = stripped.casefold()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False

    logging.getLogger("build_feed").warning(
        "Ungültiger boolescher Wert für %s=%r – verwende Default %s "
        "(erlaubt: 1/0, true/false, yes/no, on/off)",
        name,
        raw,
        default,
    )
    return default


def get_int_env(name: str, default: int) -> int:
    """Read integer environment variables safely.

    Returns the provided default if the variable is unset or cannot be
    converted to ``int``. On invalid values, a warning is logged using the
    ``build_feed`` logger.
    """

    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (ValueError, TypeError) as e:
        logging.getLogger("build_feed").warning(
            "Ungültiger Wert für %s=%r – verwende Default %d (%s: %s)",
            name,
            raw,
            default,
            type(e).__name__,
            e,
        )
        return default


ENV_ASSIGNMENT_RE = re.compile(
    r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$"
)


def _strip_quotes(value: str) -> str:
    """Return ``value`` without surrounding single or double quotes."""

    if len(value) >= 2 and ((value[0] == value[-1]) and value[0] in {'"', "'"}):
        return value[1:-1]
    return value


def _strip_inline_comment(value: str) -> str:
    """Remove inline ``#`` comments from unquoted values."""

    if not value:
        return value

    if value[0] in {'"', "'"}:
        return value

    for idx, char in enumerate(value):
        if char == "#" and (idx == 0 or value[idx - 1].isspace()):
            return value[:idx].rstrip()

    return value


def _parse_env_file(content: str) -> Dict[str, str]:
    """Parse the given env file ``content`` into a mapping."""

    parsed: Dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        match = ENV_ASSIGNMENT_RE.match(line)
        if not match:
            continue

        key, value = match.groups()
        cleaned = _strip_inline_comment(value.strip())
        parsed[key] = _strip_quotes(cleaned.strip())

    return parsed


def load_env_file(
    path: Path,
    *,
    override: bool = False,
    environ: MutableMapping[str, str] | None = None,
) -> Dict[str, str]:
    """Load environment variables from ``path`` into ``environ``.

    Returns a mapping containing the parsed assignments. Existing variables are
    left untouched unless ``override`` is set to ``True``.
    """

    env: MutableMapping[str, str]
    env = environ if environ is not None else os.environ

    if not path.exists() or not path.is_file():
        return {}

    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logging.getLogger("build_feed").warning(
            "Kann .env-Datei %s nicht lesen – überspringe sie (%s: %s)",
            path,
            type(exc).__name__,
            exc,
        )
        return {}
    parsed = _parse_env_file(content)

    for key, value in parsed.items():
        if override or key not in env:
            env[key] = value

    return parsed


def _default_env_file_candidates(base_dir: Path) -> Iterable[Path]:
    """Return default env files that should be considered for loading."""

    candidates = [
        base_dir / ".env",
        base_dir / "data" / "secrets.env",
        base_dir / "config" / "secrets.env",
    ]

    extra = os.getenv("WIEN_OEPNV_ENV_FILES")
    if extra:
        for part in extra.split(os.pathsep):
            item = part.strip()
            if not item:
                continue
            candidate = Path(item).expanduser()
            if not candidate.is_absolute():
                candidate = base_dir / candidate
            candidates.append(candidate)

    return candidates


def load_default_env_files(
    *,
    override: bool = False,
    environ: MutableMapping[str, str] | None = None,
) -> Mapping[Path, Dict[str, str]]:
    """Load standard env files relative to the project root."""

    base_dir = Path(__file__).resolve().parents[2]

    loaded: Dict[Path, Dict[str, str]] = {}
    for candidate in _default_env_file_candidates(base_dir):
        parsed = load_env_file(candidate, override=override, environ=environ)
        if parsed:
            loaded[candidate] = parsed

    return loaded

