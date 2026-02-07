"""Logging utilities for sanitizing inputs and handling sensitive data."""

from __future__ import annotations

import re
from typing import Any, List, Tuple

# Precompiled regexes for sanitization
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_LOG_INJECTION_RE = re.compile(r"[\n\r\t]")
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
    """
    Sanitize log messages by masking secrets and removing control characters.

    This protects against:
    - Leaking credentials (access IDs, tokens) in logs.
    - Log Injection attacks (newlines, ANSI sequences).

    Args:
        text: The raw message string to sanitize.
        secrets: Optional list of specific secret strings to mask.
        strip_control_chars: If True (default), newlines and other control characters
                             are escaped or removed to prevent log injection.
                             Set to False for tracebacks where readability is needed.

    Returns:
        The sanitized string.
    """
    if not text:
        return ""

    sanitized = text

    # Remove ANSI escape codes explicitly first
    sanitized = _ANSI_ESCAPE_RE.sub("", sanitized)

    # Keys that should be redacted (regex alternation, longest match first)
    _keys = (
        r"client[-_.\s]*secret|access[-_.\s]*token|refresh[-_.\s]*token|client[-_.\s]*id|signature|[a-z0-9_.\-]*password|"
        r"accessid|id[-_.\s]*token|session|apikey|[a-z0-9_.\-]*secret|ticket|[a-z0-9_.\-]*token|code|key|sig|sid|"
        r"jsessionid|phpsessid|asp\.net_sessionid|__cfduid|"
        r"authorization|auth|bearer[-_.\s]*token|[a-z0-9_.\-]*api[-_.\s]*key|auth[-_.\s]*token|"
        r"tenant[-_.\s]*id|tenant|subscription[-_.\s]*id|subscription|object[-_.\s]*id|oid|"
        r"code[-_.\s]*challenge|code[-_.\s]*verifier|"
        r"x[-_.\s]*api[-_.\s]*key|ocp[-_.\s]*apim[-_.\s]*subscription[-_.\s]*key|"
        r"[a-z0-9_.\-]*credential|x[-_.\s]*amz[-_.\s]*credential|x[-_.\s]*amz[-_.\s]*security[-_.\s]*token|"
        r"x[-_.\s]*amz[-_.\s]*signature|x[-_.\s]*auth[-_.\s]*token"
    )

    # Common header-safe keys for broad redaction in Header: Value pairs
    # Explicitly supports hyphens for header style (e.g. Api-Key)
    _header_keys = (
        r"api[-_.\s]*key|token|secret|signature|password|auth|session|cookie|private|"
        r"credential|client[-_.\s]*id"
    )

    # Common patterns for secrets in URLs/Headers
    patterns: List[Tuple[str, str]] = [
        # PEM blocks (keys/certs) - MUST be first to prevent partial redaction by other patterns
        (r"(-----BEGIN [A-Z ]+-----)(?:.|\n)*?(-----END [A-Z ]+-----)", r"\1***\2"),
        # Basic Auth in URLs (protocol://user:pass@host)
        (r"(?i)([a-z0-9+.-]+://)([^/@\s]+)@", r"\1***@"),
        # Query parameters (key=value or key%3dvalue)
        # Improved to handle quoted values (e.g. key="val with spaces") with escaped quotes support
        # AND improved unquoted handling to stop at next key or separator (comma/ampersand/newline)
        (rf"(?i)((?:{_keys})(?:%3d|=))((?:\"(?:\\.|[^\"\\\\])*\")|(?:'(?:\\.|[^'\\\\])*')|((?:(?!\s+[a-zA-Z0-9_.-]+=)[^&,\n])+))", r"\1***"),
        # Correctly handle escaped characters in JSON strings (regex: (?:\\.|[^"\\])* )
        (r'(?i)(\"accessId\"\s*:\s*\")((?:\\.|[^"\\\\])*)(\")', r'\1***\3'),
        (r"(?i)('accessId'\s*:\s*')((?:\\.|[^'\\\\])*)(')", r"\1***\3"),
        # Generic Authorization header (covers Bearer, Basic, and custom schemes)
        (r"(?i)(Authorization:\s*)((?:.*)(?:\n\s+.*)*)", r"\1***"),
        (r'(?i)(\"Authorization\"\s*:\s*\")((?:\\.|[^"\\\\])*)(\")', r'\1***\3'),
        (r"(?i)('Authorization'\s*:\s*')((?:\\.|[^'\\\\])*)(')", r"\1***\3"),
        # Cookie and Set-Cookie headers
        (r"(?i)((?:Set-)?Cookie:\s*)((?:.*)(?:\n\s+.*)*)", r"\1***"),
        (r'(?i)(\"(?:Set-)?Cookie\"\s*:\s*\")((?:\\.|[^"\\\\])*)(\")', r'\1***\3'),
        (r"(?i)('(?:Set-)?Cookie'\s*:\s*')((?:\\.|[^'\\\\])*)(')", r"\1***\3"),
        # Generic sensitive headers (e.g. X-Api-Key, X-Goog-Api-Key, X-Auth-Token)
        # Matches any header name containing a sensitive term
        (rf"(?i)((?:[-a-zA-Z0-9]*(?:{_header_keys})[-a-zA-Z0-9]*):\s*)((?:.*)(?:\n\s+.*)*)", r"\1***"),
        # Mask potentially leaked secrets in JSON error messages
        (rf'(?i)(\"(?:{_keys})\"\s*:\s*\")((?:\\.|[^"\\\\])*)(\")', r'\1***\3'),
        (rf"(?i)('(?:{_keys})'\s*:\s*')((?:\\.|[^'\\\\])*)(')", r"\1***\3"),
    ]
    for pattern, repl in patterns:
        sanitized = re.sub(pattern, repl, sanitized)

    # Mask explicit secrets provided
    if secrets:
        for secret in secrets:
            if secret:
                sanitized = sanitized.replace(secret, "***")

    # Prevent log injection by escaping newlines and control characters
    if strip_control_chars:
        # We escape common control chars to keep the log readable but safe
        sanitized = sanitized.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
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
