"""Message cleaning and size bounds for the feed run report.

Moved verbatim out of ``src/feed/reporting.py`` (700-line rule, 2026-11-02);
import these names from ``src.feed.reporting``, which re-exports them.
"""
from __future__ import annotations

import re

from ..utils.logging import sanitize_log_message

# Security (Log-Injection Drift Round 5): byte-exact mirror of
# ``src/utils/logging.py:_CONTROL_CHARS_RE``. The pre-fix narrow set
# ``[\x00-\x1f\x7f]`` covered ASCII C0 + DEL only; the canonical floor
# also covers the 8-bit C1 controls (Round 3 widening), the BiDi /
# zero-width / line-terminator family (Round 2), and the BOM. The
# narrow drift was *transitively* closed by the immediate
# ``clean_message`` delegation in ``_sanitize_log_detail`` (which
# routes through the canonical ``_INVISIBLE_DANGEROUS_RE`` always-
# strip floor) but a future caller of the local symbol that bypasses
# ``clean_message`` would re-open the C1 / BiDi / zero-width hole.
# This widening pins the canonical-floor coverage at the first layer
# so the bucket-(b) deferred risk is closed proactively. The
# replacement semantics (``_CONTROL_CHARS_RE.sub(" ", detail)`` —
# replace with SPACE to preserve token boundaries) are unchanged;
# the post-fix regex matches more characters but still replaces
# them with SPACE, and the downstream ``clean_message`` whitespace-
# collapse step folds multiple spaces back to one. Inventory
# invariant pinned by
# ``tests/test_sentinel_reporting_control_chars_re_canonical_drift.py``.
# 2026-05-14 "Cf-Format Drift": widened in lockstep with the canonical
# ``src/utils/logging.py:_CONTROL_CHARS_RE`` to cover the remaining
# 13 Unicode Cf-class bands (44 code points): U+00AD SOFT HYPHEN,
# U+0600..U+0605 Arabic prefix marks, U+06DD, U+070F, U+0890..U+0891,
# U+08E2, U+206A..U+206F deprecated BiDi controls (folds the
# existing U+2060..U+2069 band into U+2060..U+206F),
# U+FFF9..U+FFFB INTERLINEAR ANNOTATION, U+110BD/U+110CD KAITHI,
# U+13430..U+1343F EGYPTIAN HIEROGLYPH, U+1BCA0..U+1BCA3 SHORTHAND
# FORMAT, and U+1D173..U+1D17A MUSICAL SYMBOL formatting. All 44
# code points are Unicode general category Cf (Format) - same family
# as ZWSP/ZWNJ/ZWJ already in the floor, with zero advance width
# in every conforming renderer and no legitimate consumer in this
# codebase's Vienna-OePNV data path.
_CONTROL_CHARS_RE = re.compile(
    r"[\x00-\x1f\x7f-\x9f"
    r"\u00ad\u0600-\u0605\u061c\u06dd\u070f\u0890\u0891\u08e2\u180e"
    r"\u200b-\u200f\u2028-\u202e\u2060-\u206f"
    r"\ufe00-\ufe0f\ufeff\ufff9-\ufffb"
    r"\U000110bd\U000110cd"
    r"\U00013430-\U0001343F"
    r"\U0001bca0-\U0001bca3"
    r"\U0001d173-\U0001d17a"
    r"\U000e0000-\U000e007f\U000e0100-\U000e01ef]"
)

# Defence-in-depth bounds for error / warning collection. A misbehaving
# upstream that returns malformed payloads in a tight loop, or a flapping
# DNS rebinding scenario, can append a unique error per request. Without a
# cap this:
#  • blows past GitHub's ~65 KB issue-body limit (Auto-Issue submission
#    silently 422s, defeating the alerting purpose),
#  • bloats ``feed_health.json`` (a public artefact),
#  • grows the in-memory state of every long-running build process.
# 2000 chars per message keeps even a Python traceback fully readable;
# 100 unique messages is a generous ceiling well above the typical
# single-digit count and still leaves ~200 KB headroom for the rest of
# the report payload.
_MAX_REPORT_MESSAGE_LENGTH = 2000
_MAX_REPORT_MESSAGE_COUNT = 100
_REPORT_TRUNCATION_MARKER = "… [gekürzt]"


def _bounded_message(message: str) -> str:
    """Truncate ``message`` to the per-report length cap with a marker."""
    if len(message) <= _MAX_REPORT_MESSAGE_LENGTH:
        return message
    keep = _MAX_REPORT_MESSAGE_LENGTH - len(_REPORT_TRUNCATION_MARKER)
    return message[:keep] + _REPORT_TRUNCATION_MARKER


def clean_message(message: str | None) -> str:
    """Normalize log and status messages for human consumption."""

    if not message:
        return ""

    # Security: Redact secrets before processing.
    # We keep control chars here (strip_control_chars=False) because re.sub below handles whitespace collapsing.
    sanitized = sanitize_log_message(message, strip_control_chars=False)

    return re.sub(r"\s+", " ", sanitized).strip()


def _sanitize_log_detail(detail: str) -> str:
    """Sanitize log details to avoid control characters/log injection."""
    if not detail:
        return ""
    sanitized = _CONTROL_CHARS_RE.sub(" ", detail)
    return clean_message(sanitized)


__all__ = [
    "_CONTROL_CHARS_RE",
    "_MAX_REPORT_MESSAGE_LENGTH",
    "_MAX_REPORT_MESSAGE_COUNT",
    "_REPORT_TRUNCATION_MARKER",
    "_bounded_message",
    "clean_message",
    "_sanitize_log_detail",
]
