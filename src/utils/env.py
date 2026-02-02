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


def _parse_value(value: str) -> str:
    """Parse a value string, handling quotes and inline comments."""
    value = value.strip()
    if not value:
        return ""

    quote_char = None
    if value.startswith("'"):
        quote_char = "'"
    elif value.startswith('"'):
        quote_char = '"'

    if quote_char:
        parts = []
        idx = 1
        length = len(value)
        while idx < length:
            char = value[idx]

            if char == quote_char:
                return "".join(parts)

            if char == "\\":
                if idx + 1 < length:
                    next_char = value[idx + 1]
                    if quote_char == '"':
                        # Unescape double-quote and backslash in double-quoted strings
                        if next_char == '"' or next_char == "\\":
                            parts.append(next_char)
                            idx += 2
                            continue
                    elif quote_char == "'":
                        # In single quotes, we allow \' to NOT close the string,
                        # but we keep the backslash (legacy behavior compatibility)
                        if next_char == "'":
                            parts.append("\\")
                            parts.append("'")
                            idx += 2
                            continue

            parts.append(char)
            idx += 1

        # If no closing quote found, return as is (consistent with flexible parsing)
        return value
    else:
        # Unquoted: stop at first #
        return value.split("#", 1)[0].strip()


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
        parsed[key] = _parse_value(value)

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
