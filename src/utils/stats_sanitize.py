"""CSV formula-injection and control-character defence of the stats writers.

Moved verbatim from ``src/utils/stats.py`` (700-line limit, 2026-11-02);
``src.utils.stats`` re-exports every name.
"""
from __future__ import annotations

import re
from typing import Final

# CSV formula-injection (CWE-1236, OWASP "CSV Injection") defence.
#
# Excel, LibreOffice Calc, and Google Sheets evaluate any cell whose
# content begins with one of these characters as a *formula* on file
# open. The append-only stats writers persist three operator-/upstream-
# influenced text fields (``provider``, ``location_name`` for the
# disruption ledger; ``direction`` for the Stammstrecke ledger) — each
# is the boundary where defence-in-depth must clamp a payload that
# could otherwise have been planted via a poisoned cache file
# (``cache/wl/*.json`` re-emits ``ev["source"]`` verbatim into
# ``provider`` — see ``src/providers/wl_fetch.py`` lines 736 and 858),
# a poisoned station directory (``data/stations.json`` flows through
# ``display_name`` into ``direction``), or a future loosening of
# :func:`extract_location_name`'s directory-anchored gate. The
# OWASP-recommended neutralisation is a leading single quote ``'``,
# which spreadsheets render as plain text (the apostrophe itself is
# hidden in display) — defanged but still visible to operators
# scanning the CSV for indicators of compromise.
_CSV_FORMULA_PREFIXES: Final = ("=", "+", "-", "@", "\t", "\r")
# C0/C1 control-byte + BiDi / zero-width / line-terminator stripper.
#
# The character class union mirrors the canonical sanitiser
# :data:`src.utils.text._MARKDOWN_NORMALISE_UNSAFE_RE` (and its parent
# :data:`src.utils.logging._INVISIBLE_DANGEROUS_RE`) so the four
# orthogonal threat classes are closed at the CSV write boundary too:
#
# 1. **C0 controls** (``\x00-\x08`` + ``\x0B-\x0C`` + ``\x0E-\x1F``)
#    plus DEL (``\x7F``). NUL silently truncates fields in some
#    downstream CSV reader variants; BEL / VT / FF / SI / SO mangle
#    operator-facing terminal output. Excludes TAB (``\x09``), LF
#    (``\x0A``), and CR (``\x0D``) from the body since :mod:`csv`
#    already QUOTE_MINIMAL-wraps fields containing newlines and
#    embedded TAB is benign for the default ``,`` delimiter — *leading*
#    TAB / CR are still defanged by the formula-prefix branch below.
# 2. **C1 controls** (``\x7F-\x9F``). U+0085 NEXT LINE in particular is
#    treated as a record terminator by several CSV / SIEM splitters
#    (and by some Excel locales), splitting a single cell into multiple
#    rows downstream — same exfiltration shape as an embedded newline.
# 3. **BiDi format controls** (U+061C ALM, U+202A-U+202E
#    LRE/RLE/PDF/LRO/**RLO**, U+2066-U+2069 LRI/RLI/FSI/PDI). These are
#    the canonical CVE-2021-42574 Trojan-Source primitives. Stripping
#    them at the writer also closes a *formula-injection bypass*:
#    leading invisible characters (``<U+200B>=cmd…`` /
#    ``<U+202E>=cmd…``) survive ``str.strip()`` (they are not whitespace per
#    :func:`str.isspace`), so the downstream
#    ``cleaned.startswith(_CSV_FORMULA_PREFIXES)`` check returns
#    ``False`` and the apostrophe-defang is never applied. Excel /
#    LibreOffice Calc / Google Sheets render the cell with the
#    invisible prefix collapsed and may still evaluate the residual
#    ``=cmd…`` as a formula. The fix shape — strip the invisible
#    prefix at sanitiser entry — re-aligns the formula-prefix check
#    with the visible cell content.
# 4. **Zero-width characters** (U+200B-U+200F ZWSP/ZWNJ/ZWJ + LRM/RLM,
#    U+FEFF BOM). Same formula-injection bypass surface as the BiDi
#    formatting controls; LRM/RLM are full BiDi primitives despite
#    being zero-width.
#
# The pre-fix regex covered only (1); the BiDi-Mark Drift family
# (Rounds 2-4) widened ``_CONTROL_CHARS_RE``,
# ``_INVISIBLE_DANGEROUS_RE``, ``_MARKDOWN_NORMALISE_UNSAFE_RE``,
# ``_UNSAFE_CHARS_RE``, and ``_UNSAFE_URL_CHARS`` to cover (2)-(4) but
# explicitly deferred this CSV writer's regex. The inventory test
# ``test_csv_control_chars_regex_covers_canonical_invisible_dangerous_set``
# in ``tests/test_sentinel_csv_formula_injection_invisible_prefix.py``
# pins the invariant programmatically — any future widening of the
# canonical log-sanitiser fails the test until this regex widens too.
# 2026-05-11 "Tag-Character / Variation-Selector Drift": widened in
# lockstep with the canonical ``_INVISIBLE_DANGEROUS_RE`` union to
# cover the Unicode Tag block (U+E0000..U+E007F), the BMP Variation
# Selectors (U+FE00..U+FE0F), and the supplementary Variation
# Selectors (U+E0100..U+E01EF). Tag-character variants of a provider
# / location name silently fracture downstream pivot-table analytics:
# Excel / LibreOffice Calc / Google Sheets render the invisible-tag
# variant as visually-identical text but aggregate it as a distinct
# cell from the visible cousin. Closing the variant gap at the CSV
# writer keeps the operator's analytics view consistent with the
# rendered cells.
# 2026-05-14 "Zero-Width Format Drift": widened in lockstep with the
# canonical _INVISIBLE_DANGEROUS_RE union to cover U+180E (MONGOLIAN
# VOWEL SEPARATOR) and U+2060..U+2064 (WORD JOINER, FUNCTION
# APPLICATION, INVISIBLE TIMES, INVISIBLE SEPARATOR, INVISIBLE PLUS).
# Pre-fix invisible-Format variants of a provider/location name
# silently fractured pivot-table aggregation in the committed CSV
# stats ledger (Excel/LibreOffice/Sheets render the bytes as zero
# width, so operators see two visually identical cells aggregating
# as distinct keys). The U+2060..U+2069 expansion folds the existing
# BiDi-isolate band into the new range; reserved U+2065 has no
# defined meaning so the additive strip is safe.
# 2026-05-14 "Cf-Format Drift": widened in lockstep with the canonical
# _INVISIBLE_DANGEROUS_RE union to cover the remaining 13 Unicode
# Cf-class bands (51 code points): U+00AD SOFT HYPHEN, U+0600..U+0605
# Arabic prefix marks, U+06DD, U+070F, U+0890..U+0891, U+08E2,
# U+206A..U+206F deprecated BiDi controls (folds the existing
# U+2060..U+2069 band into U+2060..U+206F), U+FFF9..U+FFFB INTERLINEAR
# ANNOTATION, U+110BD/U+110CD KAITHI, U+13430..U+1343F EGYPTIAN
# HIEROGLYPH, U+1BCA0..U+1BCA3 SHORTHAND FORMAT, and U+1D173..U+1D17A
# MUSICAL SYMBOL formatting. Pre-fix Cf-class invisible variants of
# a provider/location name silently fractured pivot-table aggregation
# (Excel/LibreOffice/Sheets render Cf bytes as zero width). SOFT
# HYPHEN especially is the canonical "invisible-by-default" character
# used in real-world dedup-key spoofing attacks since 2018.
_CSV_CONTROL_CHARS_RE: Final = re.compile(
    r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x9F"
    r"\u00ad\u0600-\u0605\u061c\u06dd\u070f\u0890\u0891\u08e2\u180e"
    r"\u200b-\u200f\u2028-\u202e\u2060-\u206f"
    r"\ufe00-\ufe0f\ufeff\ufff9-\ufffb"
    r"\U000110bd\U000110cd"
    r"\U00013430-\U0001343F"
    r"\U0001bca0-\U0001bca3"
    r"\U0001d173-\U0001d17a"
    r"\U000e0000-\U000e007f\U000e0100-\U000e01ef]"
)
# Hard cap on persisted text-field length. Generous enough that any
# legitimate provider/location/direction string survives untouched;
# tight enough that an attacker who lands an unbounded string into one
# of the upstream fields cannot inflate ``data/stats/*.csv`` past a
# predictable per-row footprint. The location-name heuristic already
# caps at 80 in :func:`_normalise_location`; this is a second-layer
# clamp at the CSV boundary itself.
_CSV_TEXT_FIELD_MAX_LEN: Final = 200


def _sanitize_csv_text_field(value: str) -> str:
    """Neutralise spreadsheet formula injection in *value*.

    Pipeline:

    1. Strip control characters (the regex preserves embedded TAB / LF /
       CR — :mod:`csv` already QUOTE_MINIMAL-wraps newlines, embedded
       TAB is benign for the default ``,`` delimiter, *leading* TAB / CR
       are still defanged in step 4).
    2. Strip leading/trailing whitespace. Performed *before* the
       formula-prefix check so a payload like ``"   =cmd"`` (leading
       whitespace as a known evasion vector — some CSV consumers and
       spreadsheet importers trim whitespace before evaluating) cannot
       slip past the prefix branch and then have its whitespace
       collapsed by a downstream ``.strip()``.
    3. Cap length at :data:`_CSV_TEXT_FIELD_MAX_LEN` (defends against
       an unbounded operator/upstream string inflating ``data/stats``).
    4. Prepend a single quote (``'``) to any value beginning with one
       of :data:`_CSV_FORMULA_PREFIXES`. The leading apostrophe is
       hidden in spreadsheet display but forces the cell to be parsed
       as text; operators scanning the raw CSV still see the (defanged)
       payload, which preserves the indicator-of-compromise signal.
    """
    cleaned = _CSV_CONTROL_CHARS_RE.sub("", value).strip()
    if len(cleaned) > _CSV_TEXT_FIELD_MAX_LEN:
        cleaned = cleaned[:_CSV_TEXT_FIELD_MAX_LEN].rstrip()
    if cleaned.startswith(_CSV_FORMULA_PREFIXES):
        return "'" + cleaned
    return cleaned


__all__ = [
    "_CSV_CONTROL_CHARS_RE",
    "_CSV_FORMULA_PREFIXES",
    "_CSV_TEXT_FIELD_MAX_LEN",
    "_sanitize_csv_text_field",
]
