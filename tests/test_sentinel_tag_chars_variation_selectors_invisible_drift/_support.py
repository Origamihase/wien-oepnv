"""Hilfen und Fixtures für die Tests in diesem Paket.

Früher in tests/test_sentinel_tag_chars_variation_selectors_invisible_drift.py.
"""

from __future__ import annotations


from src.utils import logging as canonical_logging


# Spot-check code points across each new range. The inventory tests
# below assert *every* code point in each range is covered; these per-
# code-point tests focus the failure messages on representative
# boundary cases.
_MISSING_TAG_CHARS: tuple[tuple[str, str], ...] = (
    ("\U000e0000", "Tag block start (U+E0000)"),
    ("\U000e0001", "LANGUAGE TAG (U+E0001, deprecated)"),
    ("\U000e0020", "TAG SPACE (U+E0020, base of printable tag-ASCII)"),
    ("\U000e0041", "TAG LATIN CAPITAL LETTER A (U+E0041)"),
    ("\U000e0065", "TAG LATIN SMALL LETTER E (U+E0065)"),
    ("\U000e007e", "TAG TILDE (U+E007E, top of printable tag-ASCII)"),
    ("\U000e007f", "CANCEL TAG (U+E007F, Tag terminator)"),
)

_MISSING_VS_BMP: tuple[tuple[str, str], ...] = (
    ("︀", "VARIATION SELECTOR-1 (U+FE00)"),
    ("︁", "VARIATION SELECTOR-2 (U+FE01)"),
    ("︎", "VARIATION SELECTOR-15 (U+FE0E, text presentation)"),
    ("️", "VARIATION SELECTOR-16 (U+FE0F, emoji presentation)"),
)

_MISSING_VS_SUPPLEMENTARY: tuple[tuple[str, str], ...] = (
    ("\U000e0100", "VARIATION SELECTOR-17 (U+E0100)"),
    ("\U000e0101", "VARIATION SELECTOR-18 (U+E0101)"),
    ("\U000e01ee", "VARIATION SELECTOR-255 (U+E01EE)"),
    ("\U000e01ef", "VARIATION SELECTOR-256 (U+E01EF, top of plane-14 VS)"),
)

_ALL_MISSING_POINTS: tuple[tuple[str, str], ...] = (
    _MISSING_TAG_CHARS + _MISSING_VS_BMP + _MISSING_VS_SUPPLEMENTARY
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
    "_ALL_MISSING_POINTS",
    "_MISSING_TAG_CHARS",
    "_MISSING_VS_BMP",
    "_MISSING_VS_SUPPLEMENTARY",
    "_PRE_FIX_COVERED_POINTS",
    "_canonical_code_points",
]
