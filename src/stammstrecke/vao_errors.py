"""Diagnostics extracted from VAO error responses (bounded, redacted).

Moved verbatim from ``scripts/update_stammstrecke_status.py``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Final

import requests

from src.utils.files import loads_finite

# ---- Cache field-preservation validators ---------------------------------

# XML 1.0 control characters that have no readability value in a preserved
# cache field. Mirrors ``src/build_feed.py:_CONTROL_RE`` so the per-field
# shape validators reject the same control-character set the canonical
# 2026-05-09: the events.json cache has been retired. Feed events are
# now derived from the CSV ledger by :mod:`src.feed.stammstrecke`, so
# this script no longer carries any first-seen / identity / event
# building logic. The previous helpers
# (``_is_valid_preserved_*``, ``_read_existing_first_seen``,
# ``_resolve_first_seen``, ``_build_event``, ``_write_cache``) plus
# the ``OUTPUT_PATH`` constant were removed in the same pass.

# Hard cap on the ``errorCode`` string length. Real VAO codes are
# ~4-8 chars (``H890``, ``H892``, ``H730``, ``SVC_LOC_INVALID``); the
# 64-char ceiling is generous headroom while preventing a planted
# upstream from poisoning a structured log record with a multi-KiB
# blob via the otherwise-trusted error-body extraction path.
_ERROR_CODE_MAX_LEN: Final = 64

# Hard cap on the JSON body we will load into memory before scanning
# for ``errorCode``. The legitimate VAO error envelope is <1 KiB; 16
# KiB is ~16x headroom for any plausible future evolution while still
# preventing a planted upstream from amplifying the diagnostic-logging
# branch into a memory-pressure DoS. ``response.content`` would
# otherwise materialise the entire (potentially huge) error body.
_ERROR_BODY_MAX_BYTES: Final = 16 * 1024

# Strict character whitelist for the rendered ``errorCode``. Real VAO
# codes are ``[A-Za-z][A-Za-z0-9_]*`` shape (``H890``, ``H892``,
# ``H730``, ``SVC_LOC_INVALID``, ``API_GEN``…). The 2026-05-09 cron
# run revealed that the upstream sometimes echoes the supplied
# ``accessId`` into the ``errorCode`` field on bad-request responses
# (``HTTP 400`` with the accessId verbatim in the body — GitHub
# Actions then masked the log line as ``errorCode=***``, defeating
# the purpose of the diagnostic). The strict regex bails to
# ``"<malformed>"`` whenever the upstream value is not a canonical
# short code, so the diagnostic CANNOT inadvertently surface
# user-supplied or upstream-echoed secrets even when GitHub Actions'
# secret-masker is unavailable.
_VAO_ERROR_CODE_RE: Final = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")

# Hard cap on the rendered body-keys diagnostic. Real VAO error
# envelopes carry ~3-5 top-level keys (``errorCode``, ``errorText``,
# ``Message``, ``serverVersion``, ``planRtTs``); 256 chars is generous
# headroom while bounding the worst case where a planted upstream
# returns thousands of single-letter top-level keys.
_BODY_KEYS_MAX_LEN: Final = 256


# Sentinel strings for the diagnostic logging branch.
#
# Why brackets-and-uppercase-words:
# The 2026-05-09 cron run revealed that the project's ``SafeFormatter``
# (or one of its delegated maskers) treats lowercase alphanumeric
# tokens like ``unknown`` as "potential secret"-shaped and replaces
# them with ``***``. The original ``<unknown>`` sentinel therefore
# became ``***`` in the live log line, defeating the purpose of the
# diagnostic. Switching to ``[BRACKET_TAG]``-shape tokens (uppercase,
# underscores, hard delimiters) keeps the sentinels safely outside
# any token-shape heuristic the masker applies.
_DIAG_NO_RESPONSE: Final = "[NO_RESPONSE]"
_DIAG_MISSING: Final = "[MISSING]"
_DIAG_BAD_SHAPE: Final = "[BAD_SHAPE]"
_DIAG_EMPTY_BODY: Final = "[EMPTY_BODY]"
_DIAG_NO_KEYS: Final = "[NO_KEYS]"
_DIAG_REDACTED_KEY: Final = "[REDACTED]"


def _extract_http_status(exc: requests.HTTPError) -> str:
    """Return the HTTP status code from *exc* as a stringy diagnostic.

    Falls back to :data:`_DIAG_NO_RESPONSE` when the exception carries
    no response object (network-level failure, redirect-loop break,
    etc.).
    """
    response = getattr(exc, "response", None)
    if response is None:
        return _DIAG_NO_RESPONSE
    status = getattr(response, "status_code", None)
    if not isinstance(status, int):
        return _DIAG_MISSING
    return str(status)


def _extract_response_header(exc: requests.HTTPError, name: str) -> str:
    """Return the value of the *name* header from *exc.response*, or a sentinel.

    The headers we surface (``Content-Type``, ``Content-Length``,
    ``Server``, ``WWW-Authenticate``) are server-set and do NOT carry
    user-controlled content — logging them is leak-free. The value is
    truncated at :data:`_BODY_KEYS_MAX_LEN` to bound a planted-huge-
    header poisoning shape (an upstream that returns a multi-KiB
    ``Server`` header would otherwise pollute the structured log
    record).
    """
    response = getattr(exc, "response", None)
    if response is None:
        return _DIAG_NO_RESPONSE
    headers = getattr(response, "headers", None)
    if headers is None:
        return _DIAG_MISSING
    value = headers.get(name)
    if not isinstance(value, str):
        return _DIAG_MISSING
    stripped = value.strip()
    if not stripped:
        return _DIAG_MISSING
    if len(stripped) > _BODY_KEYS_MAX_LEN:
        return stripped[:_BODY_KEYS_MAX_LEN] + "…"
    return stripped


def _decode_error_body(exc: requests.HTTPError) -> Mapping[str, Any] | None:
    """Decode the response body as a top-level JSON mapping, or return ``None``.

    Bounded by :data:`_ERROR_BODY_MAX_BYTES` to prevent a planted
    upstream from amplifying the diagnostic-logging branch into a
    memory-pressure DoS.
    """
    response = getattr(exc, "response", None)
    if response is None:
        return None
    raw = getattr(response, "content", None)
    if not isinstance(raw, (bytes, bytearray)):
        return None
    if len(raw) == 0 or len(raw) > _ERROR_BODY_MAX_BYTES:
        return None
    try:
        # Security: ``loads_finite`` pins parse_constant + parse_float
        # hooks (Round 1503 sibling). VAO error envelopes carry an
        # ``errorCode`` field that the caller strict-matches against a
        # short alphabet, but the broader envelope can carry additional
        # numeric fields under a future schema — pin the canonical
        # defence here too so a hostile error response does not seed
        # non-finite floats into the diagnostic-logging surface.
        body = loads_finite(raw)
    except (ValueError, RecursionError, UnicodeDecodeError):
        return None
    if not isinstance(body, Mapping):
        return None
    return body


def _extract_vao_error_code(exc: requests.HTTPError) -> str:
    """Return the ``errorCode`` from a VAO error-envelope JSON body.

    Strict-match against :data:`_VAO_ERROR_CODE_RE` (canonical short
    HAFAS code shape) — a value that does not match (e.g. a free-form
    error message that happens to echo upstream-controlled content,
    including the supplied ``accessId``) collapses to
    :data:`_DIAG_BAD_SHAPE` rather than risking a secret leak through
    the diagnostic line.

    Falls back to :data:`_DIAG_MISSING` for every other failure mode
    (no response, body too large, body not JSON, body not a mapping,
    errorCode field absent or non-stringy).
    """
    body = _decode_error_body(exc)
    if body is None:
        return _DIAG_MISSING
    raw_code = body.get("errorCode") or body.get("error")
    if not isinstance(raw_code, str):
        return _DIAG_MISSING
    code = raw_code.strip()
    if not code:
        return _DIAG_MISSING
    if not _VAO_ERROR_CODE_RE.match(code):
        # Defensive bail: the upstream value is NOT a canonical short
        # code — most plausibly a verbose error message whose contents
        # cannot be safely surfaced. The body-keys diagnostic from
        # :func:`_describe_error_body_keys` will still expose enough
        # structural shape information to triage the failure.
        return _DIAG_BAD_SHAPE
    return code[:_ERROR_CODE_MAX_LEN]


def _describe_error_body_keys(exc: requests.HTTPError) -> str:
    """Return a comma-separated list of top-level keys from the JSON body.

    The keys are alphabetised so the diagnostic is stable across
    runs, capped at :data:`_BODY_KEYS_MAX_LEN` chars to bound a
    planted-huge-key-set body, and filtered through
    :func:`_VAO_ERROR_CODE_RE` so a top-level key whose name itself
    smuggles upstream-controlled content (an unusual but possible
    shape) does not slip into the log line. Keys that do NOT match
    the canonical HAFAS field-name pattern are rendered as
    :data:`_DIAG_REDACTED_KEY` so the diagnostic still indicates
    "the body has N fields" without exposing what they are.

    Falls back to :data:`_DIAG_EMPTY_BODY` for non-JSON / non-mapping
    / oversize payloads, mirroring :func:`_extract_vao_error_code`.
    """
    body = _decode_error_body(exc)
    if body is None:
        return _DIAG_EMPTY_BODY
    keys: list[str] = []
    # ``Mapping[str, Any]`` from ``_decode_error_body`` guarantees the
    # iteration yields ``str`` keys; we only need to gate against the
    # canonical-shape regex.
    for key in body.keys():
        stripped = key.strip()
        if _VAO_ERROR_CODE_RE.match(stripped):
            keys.append(stripped)
        else:
            keys.append(_DIAG_REDACTED_KEY)
    keys.sort()
    rendered = ",".join(keys) or _DIAG_NO_KEYS
    if len(rendered) > _BODY_KEYS_MAX_LEN:
        return rendered[:_BODY_KEYS_MAX_LEN] + "…"
    return rendered


# Hard cap on the rendered ``errorText`` diagnostic. Real VAO error
# texts are <300 chars (1-2 sentences); 256 char ceiling is generous
# headroom while bounding a planted-huge-text amplification shape.
_ERROR_TEXT_MAX_LEN: Final = 256


def _extract_vao_error_code_length(exc: requests.HTTPError) -> str:
    """Return the length of the ``errorCode`` field as a stringy diagnostic.

    The 2026-05-09 cron run revealed that VAO occasionally puts an
    accessId-shaped value into ``errorCode``; the strict regex in
    :func:`_extract_vao_error_code` accepts it (alphanumeric, within
    the 32-char ceiling) and the project's SafeFormatter then masks
    the literal value as ``***`` — defeating the diagnostic.

    Logging the LENGTH is a leak-free signal: a 4-8 char value is a
    canonical short code (``H890``, ``API_GEN``); a 16+ char value is
    almost certainly an accessId-shaped token. The operator can then
    distinguish "VAO returned its own short code" vs "VAO is echoing
    our token back" without seeing the value itself.

    Falls back to :data:`_DIAG_EMPTY_BODY` / :data:`_DIAG_MISSING`
    for the same reasons as :func:`_extract_vao_error_code`.
    """
    body = _decode_error_body(exc)
    if body is None:
        return _DIAG_EMPTY_BODY
    raw_code = body.get("errorCode") or body.get("error")
    if not isinstance(raw_code, str):
        return _DIAG_MISSING
    return str(len(raw_code))


def _extract_vao_error_text(exc: requests.HTTPError) -> str:
    """Return the ``errorText`` field, normalised and truncated.

    Unlike :func:`_extract_vao_error_code`, the errorText field is
    intended to be human-readable diagnostic prose. A typical value is
    e.g. ``"Invalid origin location"``, ``"Authentication failed"``,
    ``"No journey found between A and B"``. When VAO echoes a
    parameter value into the message, the project's SafeFormatter
    + GitHub Actions secret-masker replace the secret with ``***``
    while preserving the surrounding text — so an operator sees
    ``"Invalid accessId: ***"`` rather than ``"Invalid accessId:
    <ACCESSID>"``.

    Control bytes / line-terminators / whitespace runs are
    normalised via the project's :func:`sanitize_log_arg` (applied
    at the call site) on top of the length cap here.

    Falls back to :data:`_DIAG_EMPTY_BODY` / :data:`_DIAG_MISSING`
    for the same reasons as :func:`_extract_vao_error_code`.
    """
    body = _decode_error_body(exc)
    if body is None:
        return _DIAG_EMPTY_BODY
    raw_text = body.get("errorText") or body.get("errorMsg")
    if not isinstance(raw_text, str):
        return _DIAG_MISSING
    text = raw_text.strip()
    if not text:
        return _DIAG_MISSING
    if len(text) > _ERROR_TEXT_MAX_LEN:
        return text[:_ERROR_TEXT_MAX_LEN] + "…"
    return text


def _extract_vao_internal_error_text(exc: requests.HTTPError) -> str:
    """Return the ``internalErrorText`` / ``internalErrorTextOut`` field.

    The 2026-05-09 cron run revealed that VAO's error envelope
    includes a richer ``internalError*`` family of fields that
    typically explains the "what went wrong" at the validator level
    (e.g. ``"Stop ID 'extId::490033400' could not be resolved"``).
    These fields are server-set diagnostic prose, mirroring the
    ``errorText`` shape — same masker behaviour, same length cap.

    Falls back to :data:`_DIAG_EMPTY_BODY` / :data:`_DIAG_MISSING`
    for the same reasons as :func:`_extract_vao_error_text`.
    """
    body = _decode_error_body(exc)
    if body is None:
        return _DIAG_EMPTY_BODY
    raw_text = (
        body.get("internalErrorText")
        or body.get("internalErrorTextOut")
    )
    if not isinstance(raw_text, str):
        return _DIAG_MISSING
    text = raw_text.strip()
    if not text:
        return _DIAG_MISSING
    if len(text) > _ERROR_TEXT_MAX_LEN:
        return text[:_ERROR_TEXT_MAX_LEN] + "…"
    return text


def _extract_vao_internal_error_code(exc: requests.HTTPError) -> str:
    """Return the ``internalErrorCode`` field's length-only fingerprint.

    VAO's ``internalErrorCode`` is a structured short-code (matches
    the same canonical regex as ``errorCode``) but the SafeFormatter
    might mask it for the same false-positive reason. Render the
    LENGTH (zero-leak) so the operator can correlate it with VAO
    documentation tables.
    """
    body = _decode_error_body(exc)
    if body is None:
        return _DIAG_EMPTY_BODY
    raw_code = body.get("internalErrorCode")
    if not isinstance(raw_code, str):
        return _DIAG_MISSING
    code = raw_code.strip()
    if not code:
        return _DIAG_MISSING
    if not _VAO_ERROR_CODE_RE.match(code):
        return _DIAG_BAD_SHAPE
    return code[:_ERROR_CODE_MAX_LEN]


def _extract_vao_request_id(exc: requests.HTTPError) -> str:
    """Return the VAO ``requestId`` from the error body, or a sentinel.

    VAO assigns each request a server-side trace ID (typically a UUID
    or a short hex token) that VAO support can use to look up the
    failure on their end. The value is server-set and does not echo
    user-controlled content — safe to log fully (with the same
    canonical-shape regex guard as :func:`_extract_vao_error_code` to
    defend against an upstream that ever ships free-form text in this
    field).
    """
    body = _decode_error_body(exc)
    if body is None:
        return _DIAG_EMPTY_BODY
    raw_id = body.get("requestId") or body.get("request_id") or body.get("id")
    if not isinstance(raw_id, str):
        return _DIAG_MISSING
    rid = raw_id.strip()
    if not rid:
        return _DIAG_MISSING
    # Allow hex / UUID-like shapes too (digits, hyphens, underscores).
    if not re.match(r"^[A-Za-z0-9_\-]{1,64}$", rid):
        return _DIAG_BAD_SHAPE
    return rid
