"""Station validation: unsafe characters in names, codes and aliases.

Moved verbatim from ``src/utils/stations_validation.py`` (700-line rule, 2026-11-02).
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence

from src.utils.station_checks.basic import _format_identifier
from src.utils.station_checks.issues import SecurityIssue


# Unsafe character class for ``stations.json`` validation. Sized as the
# UNION of (a) the legacy structural-injection set (``<``/``>`` for HTML,
# ASCII C0 controls excluding ``\t``/``\n``/``\r``) and (b) every code
# point covered by the canonical log sanitiser
# ``src/utils/logging.py:_INVISIBLE_DANGEROUS_RE``. The 2026-05-09
# BiDi-Mark Drift Round 2 audit flagged the
# divergence between the two regexes as the next drift candidate: the
# canonical sanitiser already strips ``\u061c`` (ALM), ``\u200b-\u200f``
# (ZWSP/ZWNJ/ZWJ/LRM/RLM), and ``\ufeff`` (BOM), but the validator did
# not, so a planted ``stations.json`` carrying any of those characters
# in ``name``/``bst_code``/``vor_id``/``aliases`` slipped past
# ``_find_security_issues`` and flowed verbatim into the published feed
# and operator-facing log lines (Trojan-Source / log-forging primitive
# per CVE-2021-42574). The widened set keeps the two regexes in sync;
# any future widening of ``_INVISIBLE_DANGEROUS_RE`` MUST be reflected
# here \u2014 pinned by ``test_unsafe_chars_regex_covers_canonical_invisible_dangerous_set``.
#
# 2026-05-10 "8-bit C1 / DEL Drift": ``_INVISIBLE_DANGEROUS_RE`` was
# widened to ``\x7f-\x9f`` (DEL + 32 ECMA-48 C1 controls, including
# the 8-bit terminal-escape primitives ``\x9b`` CSI / ``\x9d`` OSC /
# ``\x90`` DCS) so the ``strip_control_chars=False`` sibling sinks
# inherit the defence. The stations validator is widened in the same
# PR to mirror the new canonical floor \u2014 a planted ``stations.json``
# carrying ``\x9b...m`` in a ``name`` / ``bst_code`` / ``vor_id`` /
# ``aliases`` field would otherwise flow through to the GitHub Issue
# body the directory validator emits and trigger SGR colour
# interpretation in any 8-bit-C1-honouring terminal that views it.
# 2026-05-11 "Tag-Character / Variation-Selector Drift": widened in
# lockstep with the canonical _INVISIBLE_DANGEROUS_RE union to cover
# the Unicode Tag block (U+E0000..U+E007F), the BMP Variation
# Selectors (U+FE00..U+FE0F), and the supplementary Variation
# Selectors (U+E0100..U+E01EF). Tag bytes smuggled into a station
# name / aliases / bst_code / vor_id field by a compromised upstream
# slip past the validator and reach the published feed item that
# keys off the station name.
# 2026-05-14 "Zero-Width Format Drift": widened in lockstep with the
# canonical _INVISIBLE_DANGEROUS_RE union to cover U+180E (MONGOLIAN
# VOWEL SEPARATOR) and U+2060..U+2064 (WORD JOINER, FUNCTION
# APPLICATION, INVISIBLE TIMES, INVISIBLE SEPARATOR, INVISIBLE PLUS).
# Pre-fix a planted stations.json carrying any zero-width Format
# primitive in a name / aliases / bst_code / vor_id field would slip
# past the validator and reach the published feed item that keys off
# the station name. Aliases like "Wien Hbf<U+2060>" and "Wien Hbf"
# would aggregate as DISTINCT entries downstream because the dedup
# key is byte-equality. The U+2060..U+2069 range folds in the
# existing BiDi-isolate band; reserved U+2065 has no defined meaning.
# 2026-05-14 "Cf-Format Drift": widened in lockstep with the canonical
# _INVISIBLE_DANGEROUS_RE union to cover the remaining 13 Unicode
# Cf-class bands (51 code points): U+00AD SOFT HYPHEN (the most
# impactful omission - rendered zero-width unconditionally in every
# Markdown / RSS / terminal renderer), U+0600..U+0605 Arabic prefix
# marks, U+06DD, U+070F, U+0890..U+0891, U+08E2, U+206A..U+206F
# deprecated BiDi controls (folds the existing U+2060..U+2069 band
# into U+2060..U+206F), U+FFF9..U+FFFB INTERLINEAR ANNOTATION,
# U+110BD/U+110CD KAITHI, U+13430..U+1343F EGYPTIAN HIEROGLYPH,
# U+1BCA0..U+1BCA3 SHORTHAND FORMAT, and U+1D173..U+1D17A MUSICAL
# SYMBOL formatting. Pre-fix aliases like "Wien Hbf<U+00AD>" and
# "Wien Hbf" aggregated as DISTINCT entries downstream - the SOFT
# HYPHEN renders zero-width but the dedup key is byte-equality.
_UNSAFE_CHARS_RE = re.compile(
    r"[<>\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f"
    r"\u00ad\u0600-\u0605\u061c\u06dd\u070f\u0890\u0891\u08e2\u180e"
    r"\u200b-\u200f\u2028-\u202e\u2060-\u206f"
    r"\ufe00-\ufe0f\ufeff\ufff9-\ufffb"
    r"\U000110bd\U000110cd"
    r"\U00013430-\U0001343F"
    r"\U0001bca0-\U0001bca3"
    r"\U0001d173-\U0001d17a"
    r"\U000e0000-\U000e007f\U000e0100-\U000e01ef]"
)


def _find_security_issues(
    stations: Sequence[Mapping[str, object]]
) -> Iterator[SecurityIssue]:
    for entry in stations:
        identifier = _format_identifier(entry)
        name = str(entry.get("name", "")).strip() or "<unknown>"

        # Check name
        if _UNSAFE_CHARS_RE.search(name):
            yield SecurityIssue(
                identifier=identifier,
                name=name,
                reason=f"Unsafe characters in name: {name!r}"
            )

        # Check bst_code
        bst_code = str(entry.get("bst_code") or "")
        if _UNSAFE_CHARS_RE.search(bst_code):
            yield SecurityIssue(
                identifier=identifier,
                name=name,
                reason=f"Unsafe characters in bst_code: {bst_code!r}"
            )

        # Check vor_id
        vor_id = str(entry.get("vor_id") or "")
        if _UNSAFE_CHARS_RE.search(vor_id):
            yield SecurityIssue(
                identifier=identifier,
                name=name,
                reason=f"Unsafe characters in vor_id: {vor_id!r}"
            )

        # Check aliases
        aliases_obj = entry.get("aliases")
        if isinstance(aliases_obj, Sequence) and not isinstance(aliases_obj, str | bytes):
            for alias in aliases_obj:
                if isinstance(alias, str) and _UNSAFE_CHARS_RE.search(alias):
                    yield SecurityIssue(
                        identifier=identifier,
                        name=name,
                        reason=f"Unsafe characters in alias: {alias!r}"
                    )
