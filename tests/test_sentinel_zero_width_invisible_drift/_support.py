"""Hilfen und Fixtures für die Tests in diesem Paket.

Früher in tests/test_sentinel_zero_width_invisible_drift.py.
"""

from __future__ import annotations


from src.utils import logging as canonical_logging


# Spot-check code points across each missing band.
_MISSING_ZERO_WIDTH_FORMATS: tuple[tuple[str, str], ...] = (
    ("᠎", "MONGOLIAN VOWEL SEPARATOR (U+180E)"),
    ("⁠", "WORD JOINER (U+2060)"),
    ("⁡", "FUNCTION APPLICATION (U+2061)"),
    ("⁢", "INVISIBLE TIMES (U+2062)"),
    ("⁣", "INVISIBLE SEPARATOR (U+2063)"),
    ("⁤", "INVISIBLE PLUS (U+2064)"),
)


# Pre-fix code points covered by the canonical regex — used by the
# additive-regression tests below to assert every pre-fix-covered code
# point still matches post-fix.
_PRE_FIX_COVERED_POINTS: tuple[str, ...] = (
    "\x00", "\x07", "\x1f", "\x7f",  # C0 / DEL
    "\x80", "\x9b", "\x9f",  # C1 (incl. 8-bit CSI)
    "؜",  # ALM
    "​", "‍", "‎", "‏",  # ZWSP / ZWJ / LRM / RLM
    "‪", "‮",  # LRE / RLO
    " ", " ",  # LSEP / PSEP
    "⁦", "⁩",  # LRI / PDI
    "﻿",  # BOM
    "︀", "️",  # VS-1 / VS-16
    "\U000e0020", "\U000e007f",  # TAG SPACE / CANCEL TAG
    "\U000e0100", "\U000e01ef",  # VS-17 / VS-256
)


# ---------------------------------------------------------------------------
# Inventory invariants — every sibling regex MUST stay a superset of
# the canonical ``_INVISIBLE_DANGEROUS_RE``. Pre-fix every assert FAILS
# because the sibling regexes were narrower than the canonical floor.
# ---------------------------------------------------------------------------


def _canonical_code_points() -> list[int]:
    canonical = canonical_logging._INVISIBLE_DANGEROUS_RE
    return [cp for cp in range(0x110000) if canonical.fullmatch(chr(cp))]


__all__ = [
    "_MISSING_ZERO_WIDTH_FORMATS",
    "_PRE_FIX_COVERED_POINTS",
    "_canonical_code_points",
]
