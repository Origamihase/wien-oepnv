#!/usr/bin/env python3

"""Helpers for reading environment variables in a safe way.

The module now also provides lightweight helpers to populate environment
variables from ``.env`` style files.  This allows local development setups to
store API credentials alongside the repository without committing them to
version control.  Consumers can call :func:`load_default_env_files` before
importing provider modules to ensure secrets such as ``VOR_ACCESS_ID`` are
available.

.. warning::
    Werte in ``.env``-Dateien, die ein Raute-Zeichen (``#``) enthalten
    (z. B. Passwörter oder API-Tokens), müssen zwingend in einfache (``'``) oder
    doppelte (``"``) Anführungszeichen gesetzt werden. Andernfalls bricht der
    Parser nach Bash-Standard beim ersten ``#`` ab, und der Rest der Zeile
    wird als Kommentar ignoriert.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from collections.abc import Iterable, Mapping, MutableMapping

from .files import read_capped_text
from .logging import sanitize_log_message

__all__ = [
    "get_int_env",
    "get_bool_env",
    "read_secret",
    "load_env_file",
    "load_default_env_files",
    "sanitize_log_message",
    "MAX_ENV_FILE_BYTES",
    "MAX_SECRET_FILE_BYTES",
    "DOCKER_SECRETS_DIR",
]

# Security: per-loader byte caps for the three on-disk parsers in this
# module. Pre-fix every site used the unsafe ``Path.read_text(...)``
# shape with no size cap whatsoever — a planted huge file at the
# operator-controlled credential / .env path raised ``MemoryError`` at
# import time and crashed the entire feed-build pipeline. Each cap is
# sized at >>1000x the largest legitimate shape so the cap does NOT
# introduce a false-positive rejection of valid state:
#   - ``.env`` / ``data/secrets.env`` files are typically a few KiB at
#     most; 1 MiB is ~1000x and accommodates every legitimate
#     configuration shape.
#   - Systemd / Docker secrets are typically a single line (token,
#     password, certificate); 1 MiB is ~10000x legit and matches the
#     existing ``places/quota.py:MAX_QUOTA_FILE_BYTES`` ceiling.
MAX_ENV_FILE_BYTES = 1 * 1024 * 1024
MAX_SECRET_FILE_BYTES = 1 * 1024 * 1024

# Module-level constant so tests can monkeypatch the docker secrets
# location without spoofing the filesystem.
DOCKER_SECRETS_DIR = Path("/run/secrets")

_TRUE_VALUES = {"1", "true", "t", "yes", "y", "on"}
_FALSE_VALUES = {"0", "false", "f", "no", "n", "off"}

# Security: escape sequences recognised inside double-quoted .env values.
# Mirrors ``configuration_wizard._escape_env_value`` so the wizard's writer
# and this loader's reader agree byte-for-byte. ``\\$`` / ```` \\` ```` are
# the inverse of the shell-metacharacter escaping that prevents bash
# parameter expansion / command substitution on ``set -a; source .env``.
_DOUBLE_QUOTE_ESCAPES: dict[str, str] = {
    '"': '"',
    "\\": "\\",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "$": "$",
    "`": "`",
}


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
    # Security: ``read_capped_text`` enforces a TOCTOU-safe size cap
    # (open + ``os.fstat`` on the open fd) so a planted huge file at
    # ``$CREDENTIALS_DIRECTORY/<name>`` cannot exhaust memory at startup.
    # Pre-fix the unbounded ``read_text`` here would raise ``MemoryError``
    # past ``except (OSError, ValueError)`` and crash every script that
    # imports a provider module via ``read_secret``.
    cred_dir = os.getenv("CREDENTIALS_DIRECTORY")
    if cred_dir:
        base_dir = Path(cred_dir).resolve()
        path = (base_dir / name).resolve()
        try:
            path.relative_to(base_dir)
            if path.exists() and path.is_file():
                content = read_capped_text(
                    path,
                    MAX_SECRET_FILE_BYTES,
                    label="systemd credential",
                )
                if content is not None:
                    # Secrets are typically single-line, but strip to be safe
                    return content.strip()
        except ValueError:
            pass

    # 2. Docker Secrets
    # Security: same TOCTOU-safe cap as the systemd branch above.
    docker_base = DOCKER_SECRETS_DIR.resolve()
    docker_secret = (docker_base / name).resolve()
    try:
        docker_secret.relative_to(docker_base)
        if docker_secret.exists() and docker_secret.is_file():
            content = read_capped_text(
                docker_secret,
                MAX_SECRET_FILE_BYTES,
                label="docker secret",
            )
            if content is not None:
                return content.strip()
    except ValueError:
        pass

    # 3. Environment Variable
    return (os.getenv(name) or default).strip()


def _parse_value(value: str) -> str:
    """Parse a value string, handling quotes and inline comments.

    Double-quoted values support the escape sequences listed in
    :data:`_DOUBLE_QUOTE_ESCAPES` (``\\"``, ``\\\\``, ``\\n``, ``\\r``,
    ``\\t``, ``\\$``, ```` \\` ````). Single-quoted values support only
    ``\\'``. Unrecognised backslash sequences are left literal so the
    parser is forgiving of operator-edited files.
    """
    value = value.strip()
    if not value:
        return ""

    if value.startswith("'"):
        quote_char = "'"
    elif value.startswith('"'):
        quote_char = '"'
    else:
        # Unquoted: stop at first #
        return value.split("#", 1)[0].strip()

    parts: list[str] = []
    idx = 1
    length = len(value)
    while idx < length:
        char = value[idx]
        if char == quote_char:
            return "".join(parts)
        if char == "\\" and idx + 1 < length:
            decoded = _decode_escape(quote_char, value[idx + 1])
            if decoded is not None:
                parts.append(decoded)
                idx += 2
                continue
        parts.append(char)
        idx += 1

    # If no closing quote found, return as is (consistent with flexible parsing)
    return value


def _decode_escape(quote_char: str, next_char: str) -> str | None:
    """Return the decoded escape character, or ``None`` if unrecognised.

    Single-quoted strings only honour ``\\'``; double-quoted strings
    honour the full :data:`_DOUBLE_QUOTE_ESCAPES` mapping. Splitting
    the lookup into a helper keeps :func:`_parse_value` below the
    project's C901 complexity ceiling while preserving the
    parser's behaviour byte-for-byte.
    """
    if quote_char == '"':
        return _DOUBLE_QUOTE_ESCAPES.get(next_char)
    if quote_char == "'" and next_char == "'":
        return "'"
    return None


def _parse_env_file(content: str) -> dict[str, str]:
    """Parse the given env file ``content`` into a mapping."""
    parsed: dict[str, str] = {}

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


def _warn_if_world_readable(path: Path) -> None:
    """Emit a security warning if ``path`` has group/world-accessible bits.

    Defense-in-depth: env files routinely carry secrets (``VOR_ACCESS_ID``,
    ``FEED_GITHUB_TOKEN``, ``GOOGLE_MAPS_API_KEY``). ``configure_feed.py``
    creates them with ``0o600`` via :func:`atomic_write`, but a file produced
    outside the wizard (manual ``vi``/``scp``, copy from another host, default
    ``umask 0o022``) lands at ``0o644`` and silently exposes its contents to
    every local user. Match the SSH ``StrictModes`` heuristic: warn (not
    refuse) when any group/other bit is set so misconfiguration is surfaced
    without breaking existing setups. Skipped on non-POSIX systems where the
    mode bits don't carry the same meaning.
    """

    if os.name != "posix":
        return
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        return
    if mode & 0o077:
        logging.getLogger("build_feed").warning(
            ".env-Datei %s ist gruppen-/welt-lesbar (Modus 0o%03o); "
            "Secrets können geleakt werden – `chmod 600 %s` empfohlen.",
            path,
            mode,
            path,
        )


def load_env_file(
    path: Path,
    *,
    override: bool = False,
    environ: MutableMapping[str, str] | None = None,
) -> dict[str, str]:
    """Load environment variables from ``path`` into ``environ``.

    Returns a mapping containing the parsed assignments. Existing variables are
    left untouched unless ``override`` is set to ``True``.

    **Warning:** Values containing hash symbols (`#`) must be enclosed in single
    or double quotes (e.g., `PASSWORD="my#secret"`). Otherwise, the hash and
    everything following it will be treated as an inline comment and truncated.
    """

    env: MutableMapping[str, str]
    env = environ if environ is not None else os.environ

    if not path.exists() or not path.is_file():
        return {}

    _warn_if_world_readable(path)

    # Security: ``read_capped_text`` enforces a TOCTOU-safe size cap so a
    # planted huge .env file at any of the candidate paths
    # (``.env``, ``data/secrets.env``, ``config/secrets.env``, plus any
    # ``WIEN_OEPNV_ENV_FILES`` extras) cannot exhaust memory at startup.
    # Pre-fix the unbounded ``path.read_text(encoding="utf-8")`` here
    # would raise ``MemoryError`` past ``except (OSError,
    # UnicodeDecodeError)`` (``MemoryError`` is rooted at
    # ``BaseException``, not ``Exception``) and crash every script that
    # invokes ``load_default_env_files`` BEFORE any provider runs.
    log = logging.getLogger("build_feed")
    content = read_capped_text(
        path,
        MAX_ENV_FILE_BYTES,
        label=".env",
        logger=log,
    )
    if content is None:
        log.warning(
            "Kann .env-Datei %s nicht lesen – überspringe sie (zu groß / "
            "ungültiges UTF-8 / I/O-Fehler).",
            path,
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

            resolved_candidate = Path(os.path.abspath(candidate))
            try:
                resolved_candidate.relative_to(base_dir)
            except ValueError:
                # Disallow bypassing base_dir with absolute paths or ../
                continue

            candidates.append(resolved_candidate)

    return candidates


def load_default_env_files(
    *,
    override: bool = False,
    environ: MutableMapping[str, str] | None = None,
) -> Mapping[Path, dict[str, str]]:
    """Load standard env files relative to the project root."""

    base_dir = Path(__file__).resolve().parents[2]

    loaded: dict[Path, dict[str, str]] = {}
    for candidate in _default_env_file_candidates(base_dir):
        parsed = load_env_file(candidate, override=override, environ=environ)
        if parsed:
            loaded[candidate] = parsed

    return loaded
