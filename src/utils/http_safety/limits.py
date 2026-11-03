"""Retry-After parsing, retry options, timeouts and size limits.

Moved verbatim from ``src/utils/http.py`` (700-line rule, 2026-11-02);
import from ``src.utils.http``, which re-exports every name.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

_RETRY_AFTER_NUMERIC_RE = re.compile(r"\d+(?:\.\d+)?")


def parse_retry_after(
    header_value: str | None,
    *,
    now: datetime | None = None,
) -> float | None:
    """Parse an HTTP ``Retry-After`` header value into seconds.

    Accepts both numeric forms (``"3.5"``) and HTTP-date forms
    (``"Wed, 21 Oct 2015 07:28:00 GMT"``). Returns ``None`` when the
    header is missing, empty, or unparseable. Returned delays are
    clamped to ``>= 0``.

    The optional *now* parameter lets callers inject a fixed reference
    time for deterministic testing of the HTTP-date branch.
    """

    if header_value is None:
        return None
    header = header_value.strip()
    if not header:
        return None
    if _RETRY_AFTER_NUMERIC_RE.fullmatch(header):
        # ``float(header)`` returns ``+inf`` for ~309-digit strings (and
        # Python's ``\d`` also matches Unicode decimal digits). Reject
        # any non-finite parsed value so a hostile / malformed upstream
        # cannot persuade a caller — present or future — to ``time.sleep``
        # for effectively forever.
        parsed_seconds = float(header)
        if not math.isfinite(parsed_seconds):
            return None
        return max(0.0, parsed_seconds)
    try:
        parsed = parsedate_to_datetime(header)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    reference = now if now is not None else datetime.now(UTC)
    delta_seconds = (parsed - reference).total_seconds()
    if not math.isfinite(delta_seconds):
        return None
    return max(0.0, delta_seconds)


_DEFAULT_RETRY_OPTIONS: dict[str, Any] = {
    "total": 4,
    "backoff_factor": 0.6,
    "status_forcelist": (429, 500, 502, 503, 504),
    "allowed_methods": ("GET",),
}

# Default timeout in seconds if none is provided
DEFAULT_TIMEOUT = (3.0, 15.0)

# DNS resolution timeout in seconds
DNS_TIMEOUT = 5.0

# Limit URL length to reduce DoS risk from extremely long inputs.
MAX_URL_LENGTH = 2048

# OOM Protection: Hard maximum payload limit for HTTP requests
MAX_PAYLOAD_SIZE = 10 * 1024 * 1024
