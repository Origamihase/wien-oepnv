"""Logging utilities for sanitizing inputs and handling sensitive data."""

from __future__ import annotations

import re
from typing import Any

# Precompiled regexes for sanitization
# Strip BiDi control characters (Trojan Source: CVE-2021-42574), zero-width
# characters, and Unicode line/paragraph separators that downstream consumers
# treat as record terminators (ECMAScript-pre-2019 ``JSON.parse``/``eval``,
# the GitHub PR-comment renderer, several YAML parsers, SIEM splitters that
# key off Unicode whitespace). The character class union covers:
#   * ``\x00-\x1f`` / ``\x7f-\x9f`` \u2014 ASCII C0 + DEL + C1 controls.
#   * ``\u061c`` \u2014 Arabic Letter Mark (post-Unicode-6.3 BiDi control; same
#     display-confusion blast radius as LRM/RLM but missing from every
#     prior round of this regex).
#   * ``\u200b-\u200f`` \u2014 ZWSP / ZWNJ / ZWJ / **LRM** / **RLM**. The
#     ``\u200e``/``\u200f`` BiDi marks are the same Trojan-Source primitive
#     as the already-stripped ``\u202a-\u202e`` family: a hostile payload
#     prepends LRM/RLM to invert displayed text in a Unicode-aware terminal
#     so an operator skimming a log misreads ``user=admin drop=table`` as
#     the inverse.
#   * ``\u2028-\u202e`` \u2014 Unicode **LINE SEPARATOR** (``\u2028``) /
#     **PARAGRAPH SEPARATOR** (``\u2029``) plus the CVE-2021-42574 BiDi
#     formatting controls (LRE/RLE/PDF/LRO/RLO at ``\u202a-\u202e``).
#     ``\u2028``/``\u2029`` were the load-bearing gap \u2014 Python's regex
#     ``\\s`` matches them, but ``_CONTROL_CHARS_RE`` did not. A hostile
#     upstream JSON payload could therefore embed ``\u2028`` to forge a
#     second log record in any consumer honouring Unicode line terminators.
#   * ``\u2066-\u2069`` \u2014 LRI / RLI / FSI / PDI BiDi isolates (the second
#     half of CVE-2021-42574).
#   * ``\ufeff`` \u2014 Byte Order Mark (zero-width no-break space).
# The companion regex in ``src/utils/stations_validation.py`` uses
# ``\u2028-\u202e``; this file pins the canonical UNION (incl. ALM, LRM,
# RLM) so every WARNING/ERROR site routed through the audit walker
# (``test_sentinel_clear_text_logging_drift_utils``) inherits the same
# defence floor.
_CONTROL_CHARS_RE = re.compile(
    r"[\x00-\x1f\x7f-\x9f\u061c\u200b-\u200f\u2028-\u202e\u2066-\u2069\ufeff]"
)
# Always-strip set: invisible Unicode characters that have NO readability
# value and are pure log-injection / Trojan-Source primitives. The 2026-05-09
# round (PR #1363) added these code points to ``_CONTROL_CHARS_RE`` so the
# ``strip_control_chars=True`` (default) path strips them. The drift was the
# ``strip_control_chars=False`` branch \u2014 used by ``clean_message``,
# ``_sanitize_log_detail`` (``src/feed/reporting.py``),
# ``_sanitize_exception_msg`` (``src/utils/http.py``),
# ``SafeFormatter.formatException`` and ``SafeJSONFormatter.formatException``
# (``src/feed/logging_safe.py``) \u2014 which bypasses ``_CONTROL_CHARS_RE``
# entirely to preserve readable ``\n``/``\r``/``\t`` in tracebacks. That
# leaks the BiDi / zero-width / line-terminator family verbatim into the
# public ``feed_health.json`` artefact and the GitHub Issue body submitted
# by ``submit_auto_issue``. Stripping unconditionally (independent of the
# flag) closes every sibling path in one cut while preserving the readable
# newline contract every ``strip_control_chars=False`` caller relies on.
_INVISIBLE_DANGEROUS_RE = re.compile(
    r"[\u061c\u200b-\u200f\u2028-\u202e\u2066-\u2069\ufeff]"
)
_LOG_INJECTION_RE = re.compile(r"[\n\r\t]")
# ANSI escape codes: comprehensive matching for CSI, OSC, Fe, and 2-byte sequences
# Matches:
# 1. CSI: ESC [ ...
# 2. OSC: ESC ] ... BEL/ST
# 3. Fe (excluding [ and ]): ESC [@-Z\\^_]
# 4. Two-byte sequences: ESC [space-/] [0-~]
_ANSI_ESCAPE_RE = re.compile(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\^_]|[\x20-\x2f][\x30-\x7e])')


def sanitize_log_message(
    text: str, secrets: list[str] | None = None, strip_control_chars: bool = True
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
        r"client[-_.\s]*secret|access[-_.\s]*token|refresh[-_.\s]*token|[a-z0-9_.\-]*client[-_.\s]*id[a-z0-9_.\-]*|[a-z0-9_.\-]*signature|[a-z0-9_.\-]*password[a-z0-9_.\-]*|[a-z0-9_.\-]*e[-_.\s]*mail[a-z0-9_.\-]*|"
        r"client[-_.\s]*assertion[-_.\s]*type|client[-_.\s]*assertion|"
        # Plain `assertion` (RFC 7521/7522/7523 — SAML 2.0 / JWT Bearer Auth Grant):
        # carries a signed identity assertion that is effectively a credential.
        # The optional [a-z0-9_.\-]* prefix/suffix also captures saml_assertion,
        # subject_assertion, jwt_assertion, etc.
        r"[a-z0-9_.\-]*assertion[a-z0-9_.\-]*|"
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
        r"[a-z0-9_.\-]*otp(?:[-_][a-z0-9_.\-]*)?|[a-z0-9_.\-]*glpat(?:[-_][a-z0-9_.\-]*)?|[a-z0-9_.\-]*ghp(?:[-_][a-z0-9_.\-]*)?|"
        r"\bpass\b|\bpwd\b|\buser[-_.]?pass\b"
    )

    # Common header-safe keys for broad redaction in Header: Value pairs
    # Explicitly supports hyphens for header style (e.g. Api-Key)
    _header_keys = (
        r"api[-_.\s]*key|token|secret|signature|password|auth|session|cookie|private|"
        r"client[-_.\s]*assertion|[a-z0-9_.\-]*assertion[a-z0-9_.\-]*|"
        r"saml[-_.\s]*request|saml[-_.\s]*response|nonce|state|"
        r"credential|client[-_.\s]*id|passphrase|access[-_.\s]*key|e[-_.\s]*mail"
    )

    # Common patterns for secrets in URLs/Headers
    patterns: list[tuple[str, str]] = [
        # PEM blocks (keys/certs) - MUST be first to prevent partial redaction by other patterns
        (r"(-----BEGIN [A-Z ]+-----)(?:.|\n)*?(-----END [A-Z ]+-----)", r"\1***\2"),
        # Explicitly mask accessId (Requirement) to ensure robust redaction in tracebacks
        (r"(?i)(accessId\s*=\s*)([^&\s]+)", r"\1***"),
        # Basic Auth in URLs (protocol://user:pass@host)
        (r"(?i)([a-z0-9+.-]+://)([^/@\s]+)@", r"\1***@"),
        # Query parameters (key=value or key%3dvalue)
        # Improved to handle quoted values (e.g. key="val with spaces") with escaped quotes support
        # AND improved unquoted handling to stop at next key or separator (comma/ampersand/newline/quotes)
        (
            rf"(?i)((?:{_keys})\s*(?:%3d|=)\s*)"
            rf"((?:\"(?:\\.|[^\"\\\\])*\")|(?:'(?:\\.|[^'\\\\])*')|((?:(?!\s+[a-zA-Z0-9_.-]+\s*(?:%3d|=))[^&,\n'\"])+))",
            r"\1***",
        ),
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
        # Matches any header name containing a sensitive term. Allows underscores too.
        (rf"(?i)((?:[-a-zA-Z0-9_]*(?:{_header_keys})[-a-zA-Z0-9_]*):\s*)((?:.*)(?:\n\s+.*)*)", r"\1***"),
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

    # Always strip BiDi / zero-width / Unicode line-terminator characters.
    # These have no readability value but are documented log-injection
    # (CVE-2021-42574) and Trojan-Source primitives. Stripping unconditionally
    # closes the ``strip_control_chars=False`` sibling paths
    # (``clean_message``, ``_sanitize_log_detail``, ``_sanitize_exception_msg``,
    # ``SafeFormatter.formatException``, ``SafeJSONFormatter.formatException``)
    # while preserving the readable ``\n``/``\r``/``\t`` contract those
    # callers rely on for traceback formatting.
    sanitized = _INVISIBLE_DANGEROUS_RE.sub("", sanitized)

    # Prevent log injection by escaping newlines and control characters
    if strip_control_chars:
        # We escape common control chars to keep the log readable but safe
        sanitized = sanitized.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
        sanitized = _CONTROL_CHARS_RE.sub("", sanitized)

    return sanitized


def sanitize_log_arg(arg: Any, secrets: list[str] | None = None) -> Any:
    """
    Helper to sanitize arguments passed to logging functions.

    If the argument is a string, it is sanitized. Otherwise, it is converted to string
    and then sanitized (to ensure objects with sensitive __str__ are caught, though
    primary use case is string arguments).
    """
    if isinstance(arg, int | float):
        return arg
    if isinstance(arg, str):
        return sanitize_log_message(arg, secrets)
    return sanitize_log_message(str(arg), secrets)
