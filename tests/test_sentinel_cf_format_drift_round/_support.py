"""Hilfen und Fixtures für die Tests in diesem Paket.

Früher in tests/test_sentinel_cf_format_drift_round.py.
"""

from __future__ import annotations


# Spot-check code points across each of the 13 missing bands.
_MISSING_CF_FORMATS: tuple[tuple[str, str], ...] = (
    ("­", "SOFT HYPHEN (U+00AD)"),
    ("؀", "ARABIC NUMBER SIGN (U+0600)"),
    ("؁", "ARABIC SIGN SANAH (U+0601)"),
    ("؂", "ARABIC FOOTNOTE MARKER (U+0602)"),
    ("؃", "ARABIC SIGN SAFHA (U+0603)"),
    ("؄", "ARABIC SIGN SAMVAT (U+0604)"),
    ("؅", "ARABIC NUMBER MARK ABOVE (U+0605)"),
    ("۝", "ARABIC END OF AYAH (U+06DD)"),
    ("܏", "SYRIAC ABBREVIATION MARK (U+070F)"),
    ("࢐", "ARABIC POUND MARK ABOVE (U+0890)"),
    ("࢑", "ARABIC PIASTRE MARK ABOVE (U+0891)"),
    ("࣢", "ARABIC DISPUTED END OF AYAH (U+08E2)"),
    ("⁪", "INHIBIT SYMMETRIC SWAPPING (U+206A)"),
    ("⁫", "ACTIVATE SYMMETRIC SWAPPING (U+206B)"),
    ("⁬", "INHIBIT ARABIC FORM SHAPING (U+206C)"),
    ("⁭", "ACTIVATE ARABIC FORM SHAPING (U+206D)"),
    ("⁮", "NATIONAL DIGIT SHAPES (U+206E)"),
    ("⁯", "NOMINAL DIGIT SHAPES (U+206F)"),
    ("￹", "INTERLINEAR ANNOTATION ANCHOR (U+FFF9)"),
    ("￺", "INTERLINEAR ANNOTATION SEPARATOR (U+FFFA)"),
    ("￻", "INTERLINEAR ANNOTATION TERMINATOR (U+FFFB)"),
    ("\U000110bd", "KAITHI NUMBER SIGN (U+110BD)"),
    ("\U000110cd", "KAITHI NUMBER SIGN ABOVE (U+110CD)"),
    ("\U00013430", "EGYPTIAN HIEROGLYPH VERTICAL JOINER (U+13430)"),
    ("\U00013438", "EGYPTIAN HIEROGLYPH END SEGMENT (U+13438)"),
    ("\U0001bca0", "SHORTHAND FORMAT LETTER OVERLAP (U+1BCA0)"),
    ("\U0001bca3", "SHORTHAND FORMAT UP STEP (U+1BCA3)"),
    ("\U0001d173", "MUSICAL SYMBOL BEGIN BEAM (U+1D173)"),
    ("\U0001d17a", "MUSICAL SYMBOL END PHRASE (U+1D17A)"),
)


# ---------------------------------------------------------------------------
# Additive-regression invariants — every pre-fix-covered code point
# MUST still match post-fix. The widening must be additive.
# ---------------------------------------------------------------------------


_PRE_FIX_COVERED_POINTS: tuple[str, ...] = (
    "\x00", "\x07", "\x1f", "\x7f",
    "\x80", "\x9b", "\x9f",
    "؜",
    "᠎",
    "​", "‍", "‎", "‏",
    "‪", "‮",
    " ", " ",
    "⁠", "⁣", "⁤",
    "⁦", "⁩",
    "﻿",
    "︀", "️",
    "\U000e0020", "\U000e007f",
    "\U000e0100", "\U000e01ef",
)


__all__ = [
    "_MISSING_CF_FORMATS",
    "_PRE_FIX_COVERED_POINTS",
]
