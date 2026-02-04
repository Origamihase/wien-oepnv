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
from typing import Dict, Iterable, List, Mapping, MutableMapping

try:
    from .logging import sanitize_log_message
except ImportError:
    try:
        from utils.logging import sanitize_log_message  # type: ignore[no-redef]
    except ImportError:
        # Fallback security masker if logging module is unreachable.
        # This ensures secrets are not leaked in plaintext during import errors.
        def sanitize_log_message(text: str, secrets: List[str] | None = None) -> str:
            if not text:
                return ""
            sanitized = text

            # Comprehensive keys list mirroring src.utils.logging to ensure safety during fallback
            _keys = (
                r"client_secret|access_token|refresh_token|client_id|signature|password|"
                r"accessid|id_token|session|apikey|secret|ticket|token|code|key|sig|sid|"
                r"jsessionid|phpsessid|asp\.net_sessionid|__cfduid|"
                r"authorization|auth|bearer_token|api_key|auth_token|"
                r"tenant[-_]?id|tenant|subscription[-_]?id|subscription|object[-_]?id|oid|"
                r"code_challenge|code_verifier|"
                r"x[-_]?api[-_]?key|ocp[-_]?apim[-_]?subscription[-_]?key"
            )

            _header_keys = (
                r"api[-_]?key|token|secret|signature|password|auth|session|cookie|private|"
                r"credential|client[-_]?id"
            )

            # Simplified patterns for fallback (subset of full logging module but covering critical cases)
            patterns = [
                # URL credentials
                (r"(?i)([a-z0-9+.-]+://)([^/@\s]+)@", r"\1***@"),
                # Query params and assignments (key=value)
                (rf"(?i)((?:{_keys})(?:%3d|=))((?:\"[^\"]*\")|(?:'[^']*')|[^&\s]+)", r"\1***"),
                # JSON fields (key: "value")
                (rf'(?i)(\"(?:{_keys})\"\s*:\s*\")((?:\\\\.|[^"\\\\])*)(\")', r'\1***\3'),
                (rf"(?i)('(?:{_keys})'\s*:\s*')((?:\\\\.|[^'\\\\])*)(')", r"\1***\3"),
                # Headers
                (rf"(?i)((?:[-a-zA-Z0-9]*(?:{_header_keys})[-a-zA-Z0-9]*):\s*)([^\n\r]+)", r"\1***"),
            ]

            for pattern, repl in patterns:
                sanitized = re.sub(pattern, repl, sanitized)

            # Mask secrets explicitly provided
            if secrets:
                for secret in secrets:
                    if secret:
                        sanitized = sanitized.replace(secret, "***")
            # Escape control characters to prevent log injection
            return sanitized.replace("\n", "\\n").replace("\r", "\\r")

__all__ = [
    "get_int_env",
    "get_bool_env",
    "read_secret",
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
        sanitize_log_message(raw),
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
            sanitize_log_message(raw),
            default,
            type(e).__name__,
            sanitize_log_message(str(e)),
        )
        return default


def read_secret(name: str, default: str = "") -> str:
    """Read a secret from Systemd Credentials, Docker Secrets, or Environment Variables.

    Priority:
    1. Systemd Credentials ($CREDENTIALS_DIRECTORY/name)
    2. Docker Secrets (/run/secrets/name)
    3. Environment Variable (os.getenv)
    """
    # 1. Systemd Credentials
    cred_dir = os.getenv("CREDENTIALS_DIRECTORY")
    if cred_dir:
        base_dir = Path(cred_dir).resolve()
        path = (base_dir / name).resolve()
        try:
            path.relative_to(base_dir)
            if path.exists() and path.is_file():
                try:
                    # Secrets are typically single-line, but strip to be safe
                    return path.read_text(encoding="utf-8").strip()
                except (OSError, ValueError):
                    pass
        except ValueError:
            pass

    # 2. Docker Secrets
    docker_base = Path("/run/secrets").resolve()
    docker_secret = (docker_base / name).resolve()
    try:
        docker_secret.relative_to(docker_base)
        if docker_secret.exists() and docker_secret.is_file():
            try:
                return docker_secret.read_text(encoding="utf-8").strip()
            except (OSError, ValueError):
                pass
    except ValueError:
        pass

    # 3. Environment Variable
    return (os.getenv(name) or default).strip()


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
        parts: list[str] = []
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
                        # but we consume the backslash to return the intended value.
                        if next_char == "'":
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
