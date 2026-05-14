"""Utilities for serializing provider data structures for caching."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any


__all__ = ["scrub_trojan_source_primitives", "serialize_for_cache"]


import logging

log = logging.getLogger(__name__)


# Security (Trojan-Source / BiDi-Mark Drift Round 12): canonical attack-byte
# union covering the CVE-2021-42574 BiDi formatting controls, BiDi isolates,
# zero-width primitives + LRM/RLM/ALM, Unicode line / paragraph separators,
# the BOM / ZWNBSP, and the 8-bit C1 terminal-escape primitives (CSI, OSC,
# DCS). Byte-exact mirror of ``_INVISIBLE_DANGEROUS_RE`` in
# ``src/utils/logging.py`` and ``_MARKDOWN_NORMALISE_UNSAFE_RE`` in
# ``src/utils/text.py`` so any future widening of the canonical floor stays
# uniform across the codebase. See the comment at
# ``_INVISIBLE_DANGEROUS_RE`` for the full threat-model narrative.
#
# The character-class union is built from ASCII-only escape sequences
# (``\xNN`` / ``\uNNNN``) so the source file itself contains no
# Trojan-Source primitives — reviewing this regex via ``cat`` / ``less``
# / the GitHub web UI / IDE preview cannot itself trigger BiDi reversal.
# 2026-05-11 "Tag-Character / Variation-Selector Drift": widened in
# lockstep with ``src/utils/logging.py:_INVISIBLE_DANGEROUS_RE`` to
# include the Unicode Tag block (U+E0000..U+E007F), the BMP Variation
# Selectors (U+FE00..U+FE0F), and the supplementary Variation
# Selectors (U+E0100..U+E01EF). Each is a documented invisible-
# character primitive (Trojan-Source / steganography / prompt-
# injection smuggling); a planted upstream payload carrying any of
# them in a station name / cache event / quota state survives the
# pre-fix scrubber and lands in the committed JSON sidecar.
# 2026-05-14 "Zero-Width Format Drift": widened in lockstep with the
# canonical _INVISIBLE_DANGEROUS_RE union to cover U+180E (MONGOLIAN
# VOWEL SEPARATOR) and U+2060..U+2064 (WORD JOINER, FUNCTION
# APPLICATION, INVISIBLE TIMES, INVISIBLE SEPARATOR, INVISIBLE PLUS).
# Pre-fix a planted upstream payload carrying any of these zero-width
# Format primitives in a station name / cache event / quota state
# survived this scrubber and landed in the committed JSON sidecar
# (cache/<provider>/events.json, data/stations.json,
# data/places_quota.json). The bytes are invisible in git diff /
# GitHub web UI / IDE preview but break downstream byte-equality
# dedup keying. The U+2060..U+2069 range folds in the existing
# BiDi-isolate band; reserved U+2065 has no defined meaning so the
# additive strip is safe.
# 2026-05-14 "Cf-Format Drift": widened in lockstep with the canonical
# _INVISIBLE_DANGEROUS_RE union to cover the remaining 13 Unicode
# Cf-class bands (44 code points): U+00AD SOFT HYPHEN, U+0600..U+0605
# Arabic prefix marks, U+06DD ARABIC END OF AYAH, U+070F SYRIAC
# ABBREVIATION MARK, U+0890..U+0891 ARABIC POUND/PIASTRE MARK ABOVE,
# U+08E2 ARABIC DISPUTED END OF AYAH, U+206A..U+206F deprecated BiDi
# controls, U+FFF9..U+FFFB INTERLINEAR ANNOTATION, U+110BD/U+110CD
# KAITHI NUMBER SIGN, U+13430..U+13438 EGYPTIAN HIEROGLYPH
# formatting controls, U+1BCA0..U+1BCA3 SHORTHAND FORMAT, and
# U+1D173..U+1D17A MUSICAL SYMBOL BEGIN/END BEAM/TIE/SLUR/PHRASE.
# Pre-fix any of these Cf primitives planted in a cache event /
# station name / quota state survived this scrubber and landed in
# the committed JSON sidecar - SOFT HYPHEN especially is the
# canonical "invisible-by-default" character used in real-world
# IDN / package-name / dedup-key spoofing attacks since 2018.
_TROJAN_SOURCE_PRIMITIVES_RE = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f"
    r"\u00ad\u0600-\u0605\u061c\u06dd\u070f\u0890\u0891\u08e2\u180e"
    r"\u200b-\u200f\u2028-\u202e\u2060-\u206f"
    r"\ufe00-\ufe0f\ufeff\ufff9-\ufffb"
    r"\U000110bd\U000110cd"
    r"\U00013430-\U00013438"
    r"\U0001bca0-\U0001bca3"
    r"\U0001d173-\U0001d17a"
    r"\U000e0000-\U000e007f\U000e0100-\U000e01ef]"
)


def scrub_trojan_source_primitives(
    value: Any,
    *,
    _depth: int = 0,
    max_depth: int = 50,
) -> Any:
    """Recursively strip Trojan-Source / BiDi / zero-width / line-terminator
    / 8-bit C1 primitives from a JSON-shaped structure.

    Walks ``str`` / ``dict`` / ``list`` / ``tuple`` values and removes the
    canonical CVE-2021-42574 attack-byte union from every reachable string
    (values AND dict keys). Legitimate non-ASCII content - German umlauts,
    CJK, emoji, every other Unicode code point that is NOT in the
    Trojan-Source union - passes through unchanged.

    This is the ingestion-boundary defence for operator-facing JSON sidecar
    sinks (currently ``cache/<provider>/events.json`` via
    :func:`src.utils.cache.write_cache`) where the on-disk file is committed
    to ``main`` by the cron pipeline and rendered via ``cat`` / ``less`` /
    the GitHub web UI / IDE preview. ``ensure_ascii=False`` is preserved at
    the writer so legitimate German content stays compact in the diff;
    pairing it with this scrubber rejects the canonical attack-byte union
    before it reaches the serialiser.

    The scrub-and-drop semantics deliberately differ from the
    ``ensure_ascii=True`` escape-and-preserve semantics used by the sibling
    state writers (``MonthlyQuota.save_atomic``,
    ``_write_request_count_file``, ``write_status``,
    ``_write_heartbeat_file``, ``_write_quarantine_file``, ``_save_state``).
    Cache content is a forward-flowing data feed with high cardinality
    (thousands of items per refresh); per-item forensic re-construction
    would just bloat the diff. State sinks carry unique sentinels where
    forensic re-construction matters.

    A ``RecursionError`` is raised if the structure depth exceeds
    ``max_depth`` - defence-in-depth against pathological inputs that
    bypass the upstream depth-bomb guard at the JSON parser.
    """
    if _depth > max_depth:
        raise RecursionError(f"Maximum recursion depth {max_depth} exceeded")
    if isinstance(value, str):
        return _TROJAN_SOURCE_PRIMITIVES_RE.sub("", value)
    if isinstance(value, dict):
        return {
            (
                _TROJAN_SOURCE_PRIMITIVES_RE.sub("", key)
                if isinstance(key, str)
                else key
            ): scrub_trojan_source_primitives(
                val, _depth=_depth + 1, max_depth=max_depth
            )
            for key, val in value.items()
        }
    if isinstance(value, list):
        return [
            scrub_trojan_source_primitives(item, _depth=_depth + 1, max_depth=max_depth)
            for item in value
        ]
    if isinstance(value, tuple):
        return tuple(
            scrub_trojan_source_primitives(item, _depth=_depth + 1, max_depth=max_depth)
            for item in value
        )
    return value


def serialize_for_cache(
    value: Any,
    _stack: set[int] | None = None,
    _depth: int = 0,
    max_depth: int = 50,
) -> Any:
    """Recursively convert *value* into a JSON-serializable structure.

    Handles cycles by raising ValueError, similar to json.dumps.
    Protects against deep recursion (DoS) by enforcing max_depth.
    """
    # Security: Prevent stack overflow from deeply nested structures
    if _depth > max_depth:
        log.warning("Maximum recursion depth exceeded during serialization")
        raise RecursionError(f"Maximum recursion depth {max_depth} exceeded")

    # Simple types - return immediately
    if value is None or isinstance(value, str | int | float | bool):
        return value

    if isinstance(value, datetime):
        return value.isoformat()

    # Container types - check for cycles
    if isinstance(value, dict | list | tuple | set):
        if _stack is None:
            _stack = set()

        obj_id = id(value)
        if obj_id in _stack:
            raise ValueError("Circular reference detected")

        _stack.add(obj_id)
        try:
            if isinstance(value, dict):
                return {
                    key: serialize_for_cache(val, _stack, _depth + 1, max_depth)
                    for key, val in value.items()
                }
            if isinstance(value, list | tuple):
                return [
                    serialize_for_cache(item, _stack, _depth + 1, max_depth)
                    for item in value
                ]
            if isinstance(value, set):
                serialized = [
                    serialize_for_cache(item, _stack, _depth + 1, max_depth)
                    for item in value
                ]
                # Sort for deterministic output
                try:
                    return sorted(serialized, key=str)
                except TypeError:
                    # Fallback if str() fails or comparison fails
                    return serialized
        finally:
            _stack.remove(obj_id)

    # Unknown types pass through (let json.dump handle or fail later)
    return value
