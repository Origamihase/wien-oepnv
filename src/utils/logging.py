"""Logging utilities for sanitizing inputs and handling sensitive data."""

from __future__ import annotations

import re
from typing import Any, List, Tuple

# Precompiled regexes for sanitization
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_LOG_INJECTION_RE = re.compile(r"[\n\r\t]")
# ANSI escape codes: \x1b followed by [ and optional params, ending with a letter
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def sanitize_log_message(text: str, secrets: List[str] | None = None) -> str:
    """
    Sanitize log messages by masking secrets and removing control characters.

    This protects against:
    - Leaking credentials (access IDs, tokens) in logs.
    - Log Injection attacks (newlines, ANSI sequences).

    Args:
        text: The raw message string to sanitize.
        secrets: Optional list of specific secret strings to mask.

    Returns:
        The sanitized string.
    """
    if not text:
        return ""

    sanitized = text

    # Keys that should be redacted (regex alternation, longest match first)
    _keys = (
        r"client_secret|access_token|refresh_token|client_id|signature|password|"
        r"accessid|id_token|session|apikey|secret|ticket|token|code|key|sig|sid|"
        r"jsessionid|phpsessid|asp\.net_sessionid|__cfduid|"
        r"authorization|auth|bearer_token|api_key|auth_token|"
        r"tenant_id|tenant|subscription_id|subscription|object_id|oid|"
        r"code_challenge|code_verifier"
    )

    # Common header-safe keys for broad redaction in Header: Value pairs
    # Explicitly supports hyphens for header style (e.g. Api-Key)
    _header_keys = (
        r"api[-_]?key|token|secret|signature|password|auth|session|cookie|private|"
        r"credential|client[-_]?id"
    )

    # Common patterns for secrets in URLs/Headers
    patterns: List[Tuple[str, str]] = [
        # Basic Auth in URLs (protocol://user:pass@host)
        (r"(?i)([a-z0-9+.-]+://)([^/@\s]+)@", r"\1***@"),
        # Query parameters (key=value or key%3dvalue)
        (rf"(?i)((?:{_keys})(?:%3d|=))([^&\s]+)", r"\1***"),
        # Correctly handle escaped characters in JSON strings (regex: (?:\\.|[^"\\])* )
        (r'(?i)(\"accessId\"\s*:\s*\")((?:\\\\.|[^"\\\\])*)(\")', r'\1***\3'),
        (r"(?i)('accessId'\s*:\s*')((?:\\\\.|[^'\\\\])*)(')", r"\1***\3"),
        # Generic Authorization header (covers Bearer, Basic, and custom schemes)
        (r"(?i)(Authorization:\s*)([^\n\r]+)", r"\1***"),
        (r'(?i)(\"Authorization\"\s*:\s*\")((?:\\\\.|[^"\\\\])*)(\")', r'\1***\3'),
        (r"(?i)('Authorization'\s*:\s*')((?:\\\\.|[^'\\\\])*)(')", r"\1***\3"),
        # Cookie and Set-Cookie headers
        (r"(?i)((?:Set-)?Cookie:\s*)([^\n\r]+)", r"\1***"),
        (r'(?i)(\"(?:Set-)?Cookie\"\s*:\s*\")((?:\\\\.|[^"\\\\])*)(\")', r'\1***\3'),
        (r"(?i)('(?:Set-)?Cookie'\s*:\s*')((?:\\\\.|[^'\\\\])*)(')", r"\1***\3"),
        # Generic sensitive headers (e.g. X-Api-Key, X-Goog-Api-Key, X-Auth-Token)
        # Matches any header name containing a sensitive term
        (rf"(?i)((?:[-a-zA-Z0-9]*(?:{_header_keys})[-a-zA-Z0-9]*):\s*)([^\n\r]+)", r"\1***"),
        # Mask potentially leaked secrets in JSON error messages
        (rf'(?i)(\"(?:{_keys})\"\s*:\s*\")((?:\\\\.|[^"\\\\])*)(\")', r'\1***\3'),
        (rf"(?i)('(?:{_keys})'\s*:\s*')((?:\\\\.|[^'\\\\])*)(')", r"\1***\3"),
    ]
    for pattern, repl in patterns:
        sanitized = re.sub(pattern, repl, sanitized)

    # Mask explicit secrets provided
    if secrets:
        for secret in secrets:
            if secret:
                sanitized = sanitized.replace(secret, "***")

    # Prevent log injection by escaping newlines and control characters
    # We escape common control chars to keep the log readable but safe
    sanitized = sanitized.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")

    # Remove ANSI escape codes explicitly first
    sanitized = _ANSI_ESCAPE_RE.sub("", sanitized)

    # Remove remaining control characters
    sanitized = _CONTROL_CHARS_RE.sub("", sanitized)

    return sanitized


def sanitize_log_arg(arg: Any, secrets: List[str] | None = None) -> Any:
    """
    Helper to sanitize arguments passed to logging functions.

    If the argument is a string, it is sanitized. Otherwise, it is converted to string
    and then sanitized (to ensure objects with sensitive __str__ are caught, though
    primary use case is string arguments).
    """
    if isinstance(arg, (int, float)):
        return arg
    if isinstance(arg, str):
        return sanitize_log_message(arg, secrets)
    return sanitize_log_message(str(arg), secrets)
