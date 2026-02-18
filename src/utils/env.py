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

        # Precompiled regexes for sanitization (copied from src.utils.logging)
        _CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
        # ANSI escape codes: comprehensive matching for CSI, OSC, Fe, and 2-byte sequences
        # Matches:
        # 1. CSI: ESC [ ...
        # 2. OSC: ESC ] ... BEL/ST
        # 3. Fe (excluding [ and ]): ESC [@-Z\\^_]
        # 4. Two-byte sequences: ESC [space-/] [0-~]
        _ANSI_ESCAPE_RE = re.compile(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\^_]|[\x20-\x2f][\x30-\x7e])')

        def sanitize_log_message(
            text: str, secrets: List[str] | None = None, strip_control_chars: bool = True
        ) -> str:
            if not text:
                return ""
            sanitized = text

            # Remove ANSI escape codes explicitly first
            sanitized = _ANSI_ESCAPE_RE.sub("", sanitized)

            # Comprehensive keys list mirroring src.utils.logging to ensure safety during fallback
            _keys = (
                r"client[-_.\s]*secret|access[-_.\s]*token|refresh[-_.\s]*token|[a-z0-9_.\-]*client[-_.\s]*id[a-z0-9_.\-]*|[a-z0-9_.\-]*signature|[a-z0-9_.\-]*password[a-z0-9_.\-]*|[a-z0-9_.\-]*e[-_.\s]*mail[a-z0-9_.\-]*|"
                r"client[-_.\s]*assertion[-_.\s]*type|client[-_.\s]*assertion|"
                r"saml[-_.\s]*request|saml[-_.\s]*response|"
                r"[a-z0-9_.\-]*accessid[a-z0-9_.\-]*|id[-_.\s]*token|[a-z0-9_.\-]*session[-_.\s]*id[a-z0-9_.\-]*|session|cookie|[a-z0-9_.\-]*apikey[a-z0-9_.\-]*|[a-z0-9_.\-]*secret[a-z0-9_.\-]*|ticket|[a-z0-9_.\-]*token|code|key|sig|sid|"
                r"nonce|state|"
                r"jsessionid|phpsessid|asp\.net_sessionid|__cfduid|"
                r"authorization|auth|bearer[-_.\s]*token|bearer|[a-z0-9_.\-]*api[-_.\s]*key[a-z0-9_.\-]*|[a-z0-9_.\-]*private[-_.\s]*key|auth[-_.\s]*token|"
                r"tenant[-_.\s]*id|tenant|subscription[-_.\s]*id|subscription|object[-_.\s]*id|oid|"
                r"code[-_.\s]*challenge|code[-_.\s]*verifier|"
                r"x[-_.\s]*api[-_.\s]*key|ocp[-_.\s]*apim[-_.\s]*subscription[-_.\s]*key|"
                r"[a-z0-9_.\-]*credential|x[-_.\s]*amz[-_.\s]*credential|x[-_.\s]*amz[-_.\s]*security[-_.\s]*token|"
                r"x[-_.\s]*amz[-_.\s]*signature|x[-_.\s]*auth[-_.\s]*token|"
                r"[a-z0-9_.\-]*passphrase[a-z0-9_.\-]*|[a-z0-9_.\-]*access[-_.\s]*key[-_.\s]*id[a-z0-9_.\-]*|"
                r"[a-z0-9_.\-]*secret[-_.\s]*access[-_.\s]*key|[a-z0-9_.\-]*auth[-_.\s]*code[a-z0-9_.\-]*|"
                r"[a-z0-9_.\-]*authorization[-_.\s]*code[a-z0-9_.\-]*|"
                r"[a-z0-9_.\-]*otp(?:_[a-z0-9_.\-]*)?|[a-z0-9_.\-]*glpat[a-z0-9_.\-]*|[a-z0-9_.\-]*ghp[a-z0-9_.\-]*"
            )

            _header_keys = (
                r"api[-_.\s]*key|token|secret|signature|password|auth|session|cookie|private|"
                r"client[-_.\s]*assertion|saml[-_.\s]*request|saml[-_.\s]*response|nonce|state|"
                r"credential|client[-_.\s]*id|passphrase|access[-_.\s]*key|e[-_.\s]*mail"
            )

            # Simplified patterns for fallback (subset of full logging module but covering critical cases)
            patterns = [
                # PEM blocks (keys/certs)
                (r"(-----BEGIN [A-Z ]+-----)(?:.|\n)*?(-----END [A-Z ]+-----)", r"\1***\2"),
                # URL credentials
                (r"(?i)([a-z0-9+.-]+://)([^/@\s]+)@", r"\1***@"),
                # Query params and assignments (key=value)
                (
                    rf"(?i)((?:{_keys})\s*(?:%3d|=)\s*)"
                    rf"((?:\"(?:\\.|[^\"\\\\])*\")|(?:'(?:\\.|[^'\\\\])*')|((?:(?!\s+[a-zA-Z0-9_.-]+\s*(?:%3d|=))[^&,\n])+))",
                    r"\1***",
                ),
                # JSON fields (key: "value")
                (rf'(?i)(\"(?:{_keys})\"\s*:\s*\")((?:\\.|[^"\\\\])*)(\")', r'\1***\3'),
                (rf"(?i)('(?:{_keys})'\s*:\s*')((?:\\.|[^'\\\\])*)(')", r"\1***\3"),
                # Headers
                (rf"(?i)((?:[-a-zA-Z0-9_]*(?:{_header_keys})[-a-zA-Z0-9_]*):\s*)((?:.*)(?:\n\s+.*)*)", r"\1***"),
            ]

            for pattern, repl in patterns:
                sanitized = re.sub(pattern, repl, sanitized)

            # Mask secrets explicitly provided
            if secrets:
                for secret in secrets:
                    if secret:
                        sanitized = sanitized.replace(secret, "***")

            # Escape control characters to prevent log injection
            if strip_control_chars:
                # We escape common control chars to keep the log readable but safe
                sanitized = sanitized.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
                sanitized = _CONTROL_CHARS_RE.sub("", sanitized)

            return sanitized

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
        safe_raw = sanitize_log_message(raw)
        # Extra safety: redact the raw value and its repr from exception message FIRST
        # to prevent leaks from double-escaped strings in repr() which sanitize_log_message might miss/mangle
        msg_str = str(e)
        if raw:
            msg_str = msg_str.replace(repr(raw), "***").replace(raw, "***")
        safe_msg = sanitize_log_message(msg_str)

        logging.getLogger("build_feed").warning(
            "Ungültiger Wert für %s=%r – verwende Default %d (%s: %s)",
            name,
            safe_raw,
            default,
            type(e).__name__,
            safe_msg,
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
                        # Also unescape common control characters (\n, \r, \t) to support
                        # values generated by configuration_wizard and standard .env conventions.
                        if next_char == '"' or next_char == "\\":
                            parts.append(next_char)
                            idx += 2
                            continue
                        elif next_char == "n":
                            parts.append("\n")
                            idx += 2
                            continue
                        elif next_char == "r":
                            parts.append("\r")
                            idx += 2
                            continue
                        elif next_char == "t":
                            parts.append("\t")
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

    idx = 0
    length = len(content)

    while idx < length:
        # Skip leading whitespace
        while idx < length and content[idx].isspace():
            idx += 1

        if idx >= length:
            break

        # Check for comment
        if content[idx] == '#':
            # Consume until newline
            while idx < length and content[idx] != '\n':
                idx += 1
            continue

        # Read Key
        # Expect (export )? KEY =
        line_start = idx
        while idx < length and content[idx] != '=' and content[idx] != '\n':
            idx += 1

        if idx >= length or content[idx] == '\n':
            # No equals sign on this line, skip
            idx += 1
            continue

        key_part = content[line_start:idx]
        idx += 1 # Consume '='

        # Clean up key (remove export, spaces)
        key_match = re.match(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*$", key_part.strip())
        if not key_match:
            # Invalid key, skip line: consume until newline
            while idx < length and content[idx] != '\n':
                idx += 1
            continue

        key = key_match.group(1)

        # Read Value
        # Skip whitespace after = (but stop at newline)
        while idx < length and content[idx] != '\n' and content[idx].isspace():
            idx += 1

        if idx >= length:
            parsed[key] = ""
            break

        if content[idx] == '\n':
            parsed[key] = ""
            idx += 1
            continue

        # Check if quoted
        if content[idx] == '"' or content[idx] == "'":
            quote_char = content[idx]
            val_start = idx
            idx += 1

            # Find closing quote
            while idx < length:
                char = content[idx]
                if char == quote_char:
                    idx += 1
                    break

                if char == '\\' and idx + 1 < length:
                    # Escape sequence, skip next char check
                    idx += 2
                    continue

                idx += 1

            # Extracted raw value including quotes
            raw_value = content[val_start:idx]
            parsed[key] = _parse_value(raw_value)

            # Consume rest of line (expect comments or whitespace)
            while idx < length and content[idx] != '\n':
                idx += 1

        else:
            # Unquoted
            val_start = idx
            while idx < length and content[idx] != '\n' and content[idx] != '#':
                idx += 1

            raw_value = content[val_start:idx]
            parsed[key] = raw_value.strip()

            # If we stopped at #, consume comment line
            if idx < length and content[idx] == '#':
                while idx < length and content[idx] != '\n':
                    idx += 1

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
