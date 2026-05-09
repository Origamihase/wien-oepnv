"""Sentinel PoC: ``_UNSAFE_CHARS_RE`` in ``src/utils/stations_validation.py``
is narrower than the canonical ``_INVISIBLE_DANGEROUS_RE``.

The 2026-05-09 BiDi-Mark Drift Round 2 entry in ``.jules/sentinel.md``
explicitly named this gap as the next drift candidate::

    The companion regex in ``src/utils/stations_validation.py:_UNSAFE_CHARS_RE``
    is also narrower than ``_INVISIBLE_DANGEROUS_RE`` — that's a deliberate
    deferral (station-validator scope is structural validation, not log
    sanitisation), but flag it as the next drift candidate if a future
    round audits station-data flow into log emit.

Pre-fix character class::

    [<>\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\u2028-\\u202e\\u2066-\\u2069]

The set covers ASCII C0 controls (minus ``\\t``/``\\n``/``\\r``), the
line/paragraph-separator + LRE/RLE/PDF/LRO/RLO BiDi family (``\\u2028-
\\u202e``), and the LRI/RLI/FSI/PDI BiDi-isolate family (``\\u2066-
\\u2069``). It explicitly **DOES NOT** cover:

  * ``\\u061c`` — ARABIC LETTER MARK (ALM, post-Unicode-6.3 BiDi
    control). Same Trojan-Source primitive as the LRE/RLE family but
    missing from every prior round of this regex.
  * ``\\u200b-\\u200f`` — ZWSP / ZWNJ / ZWJ / **LRM** / **RLM**. The
    LRM/RLM marks are the same Trojan-Source primitive as the
    already-stripped ``\\u202a-\\u202e`` family: a planted station
    name with LRM/RLM inverts displayed text in a Unicode-aware
    terminal so an operator skimming a log line misreads the value.
    The zero-width family (ZWSP/ZWNJ/ZWJ) enables visual obfuscation
    where a station name *looks* identical to a legitimate one but
    is treated as different by the deduplication / merge logic.
  * ``\\ufeff`` — BYTE ORDER MARK (zero-width no-break space). A
    planted name with a leading BOM looks identical to the canonical
    name but has different bytes; the validator should flag it.

The canonical sanitiser ``src/utils/logging.py:_INVISIBLE_DANGEROUS_RE``
covers the full union (``\\u061c`` ALM + ``\\u200b-\\u200f`` zero-width
+ LRM/RLM + ``\\u2028-\\u202e`` separators + LRE/RLE/PDF/LRO/RLO +
``\\u2066-\\u2069`` LRI/RLI/FSI/PDI + ``\\ufeff`` BOM). The fix in this
PR widens ``_UNSAFE_CHARS_RE`` to the same union so a planted
``stations.json`` with any of the missing code points is flagged at
validation time, before the entries flow into the published feed and
operator-facing log lines.

Threat model
------------
A planted ``stations.json`` (compromised CI runner / partial flush +
power loss / corrupted previous run / parallel orchestrator process
performing an atomic state swap mid-write / hostile PR that lands a
poisoned entry) carries a station name like:

    "Wien‮ Hauptbahnhof"  # \\u202e = RTL OVERRIDE — DOES get flagged today

vs.

    "Wien‎ Hauptbahnhof"  # \\u200e = LRM            — DOES NOT get flagged today

Both have identical Trojan-Source / log-injection blast radius, but
the LRM variant slips past ``_find_security_issues``. The validator's
verdict feeds ``scripts/validate_stations.py`` which gates the cron
pipeline; a planted name with LRM therefore reaches the published
feed and operator dashboards verbatim.

Companion fix
-------------
This file pins the invariant in two layers:

  1. **PoC tests** (per code point) that exercise the validator with
     each of the missing characters and assert a security issue is
     yielded.
  2. **Inventory test** that asserts ``_UNSAFE_CHARS_RE`` matches
     every character in the canonical ``_INVISIBLE_DANGEROUS_RE`` set.
     A regression here means a future contributor either narrowed
     ``_UNSAFE_CHARS_RE`` (drift) or widened ``_INVISIBLE_DANGEROUS_RE``
     without updating the validator (drift in the opposite direction).
"""

from __future__ import annotations

import pytest

from src.utils import logging as canonical_logging
from src.utils import stations_validation


# Code points that ``_INVISIBLE_DANGEROUS_RE`` covers but pre-fix
# ``_UNSAFE_CHARS_RE`` did not. Each is a documented BiDi / zero-width /
# Trojan-Source primitive.
_MISSING_CODE_POINTS: tuple[tuple[str, str], ...] = (
    ("؜", "ARABIC LETTER MARK (ALM)"),
    ("​", "ZERO WIDTH SPACE (ZWSP)"),
    ("‌", "ZERO WIDTH NON-JOINER (ZWNJ)"),
    ("‍", "ZERO WIDTH JOINER (ZWJ)"),
    ("‎", "LEFT-TO-RIGHT MARK (LRM)"),
    ("‏", "RIGHT-TO-LEFT MARK (RLM)"),
    ("﻿", "BYTE ORDER MARK (BOM)"),
)


@pytest.mark.parametrize(
    "code_point,label",
    _MISSING_CODE_POINTS,
    ids=[label for _, label in _MISSING_CODE_POINTS],
)
def test_unsafe_chars_regex_matches_missing_code_point(
    code_point: str, label: str
) -> None:
    """Pre-fix: ``_UNSAFE_CHARS_RE`` did not match ``code_point`` so a
    planted station name carrying it slipped past
    ``_find_security_issues``. Post-fix: the regex matches the code
    point and the validator flags the issue."""
    assert stations_validation._UNSAFE_CHARS_RE.search(code_point) is not None, (
        f"_UNSAFE_CHARS_RE must match {label} ({hex(ord(code_point))}); "
        "see .jules/sentinel.md (BiDi-Mark Drift Round 2) for the full "
        "list of code points the validator must reject."
    )


@pytest.mark.parametrize(
    "code_point,label",
    _MISSING_CODE_POINTS,
    ids=[label for _, label in _MISSING_CODE_POINTS],
)
def test_find_security_issues_flags_planted_name_with_missing_code_point(
    code_point: str, label: str
) -> None:
    """End-to-end PoC: a planted ``stations.json`` entry whose ``name``
    contains ``code_point`` must be reported as a SecurityIssue."""
    poisoned = [
        {
            "bst_id": "12345",
            "name": f"Wien{code_point} Hauptbahnhof",
        }
    ]
    issues = list(stations_validation._find_security_issues(poisoned))
    assert issues, (
        f"Planted name with {label} ({hex(ord(code_point))}) must be "
        "flagged by _find_security_issues — pre-fix _UNSAFE_CHARS_RE was "
        "narrower than _INVISIBLE_DANGEROUS_RE and let these characters "
        "through."
    )
    assert any("name" in issue.reason for issue in issues), (
        "SecurityIssue must reference the offending field"
    )


@pytest.mark.parametrize(
    "code_point,label",
    _MISSING_CODE_POINTS,
    ids=[label for _, label in _MISSING_CODE_POINTS],
)
def test_find_security_issues_flags_planted_alias_with_missing_code_point(
    code_point: str, label: str
) -> None:
    """End-to-end PoC: a planted ``stations.json`` entry whose
    ``aliases`` carry ``code_point`` must be reported. Aliases flow
    through fuzzy-name matching into the merge / dedupe pipeline, so
    a poisoned alias is at least as load-bearing as a poisoned name.
    """
    poisoned = [
        {
            "bst_id": "12345",
            "name": "Wien Hauptbahnhof",
            "aliases": [f"Wien Hbf{code_point}"],
        }
    ]
    issues = list(stations_validation._find_security_issues(poisoned))
    assert issues, (
        f"Planted alias with {label} ({hex(ord(code_point))}) must be "
        "flagged by _find_security_issues — the alias drift family is "
        "as load-bearing as the name drift family."
    )
    assert any("alias" in issue.reason for issue in issues), (
        "SecurityIssue must reference the offending alias field"
    )


def test_unsafe_chars_regex_covers_canonical_invisible_dangerous_set() -> None:
    """Inventory invariant: every character that
    :data:`src.utils.logging._INVISIBLE_DANGEROUS_RE` matches MUST
    also match :data:`src.utils.stations_validation._UNSAFE_CHARS_RE`.

    A regression here means the two regexes have drifted apart again
    — either the validator was narrowed (drift) or the canonical log
    sanitiser was widened without a matching update at the validator
    boundary. Both shapes leak a planted station name carrying the
    newly-listed code point past the validator and into the published
    feed.
    """
    canonical = canonical_logging._INVISIBLE_DANGEROUS_RE
    validator = stations_validation._UNSAFE_CHARS_RE

    # Materialise every code point the canonical regex matches and
    # assert the validator regex matches the same set.
    canonical_code_points: list[int] = []
    for cp in range(0x110000):  # full Unicode BMP + supplementary planes
        if canonical.fullmatch(chr(cp)):
            canonical_code_points.append(cp)

    # Sanity: the canonical regex covers a non-trivial set.
    assert canonical_code_points, "Canonical _INVISIBLE_DANGEROUS_RE matches nothing"

    missing: list[int] = []
    for cp in canonical_code_points:
        if not validator.fullmatch(chr(cp)):
            missing.append(cp)

    assert not missing, (
        f"_UNSAFE_CHARS_RE is narrower than _INVISIBLE_DANGEROUS_RE; "
        f"missing {len(missing)} code point(s): "
        + ", ".join(f"U+{cp:04X}" for cp in missing[:20])
        + (" …" if len(missing) > 20 else "")
        + "\nThe two regexes must stay in sync: any code point covered "
        "by the canonical log sanitiser must also be flagged by the "
        "stations.json validator. See .jules/sentinel.md "
        "(BiDi-Mark Drift Round 2) for the closing rule."
    )


def test_unsafe_chars_regex_preserves_existing_coverage() -> None:
    """Regression: every character ``_UNSAFE_CHARS_RE`` matched pre-fix
    must still match post-fix. The widening MUST be additive."""
    pre_fix_must_match = (
        "<",
        ">",
        "\x00",
        "\x01",
        "\x07",
        "\x0b",
        "\x0c",
        "\x0e",
        "\x1f",
        " ",
        " ",
        "‪",
        "‫",
        "‬",
        "‭",
        "‮",
        "⁦",
        "⁧",
        "⁨",
        "⁩",
    )
    for cp in pre_fix_must_match:
        assert stations_validation._UNSAFE_CHARS_RE.search(cp) is not None, (
            f"Existing coverage must be preserved: {hex(ord(cp))} "
            "must still match _UNSAFE_CHARS_RE after the widening."
        )


def test_unsafe_chars_regex_does_not_match_safe_chars() -> None:
    """Regression: ASCII letters / digits / common punctuation that
    legitimate station names carry (e.g. umlauts, hyphens, parentheses,
    spaces) must NOT match the widened regex. The fix must not be a
    super-set that turns every legitimate name into a security issue.
    """
    safe_chars = "Wien Hauptbahnhof - Praterstern Westbahnhof Höchstädtplatz Wien Süd 1234567890.,-/()"
    for ch in safe_chars:
        assert stations_validation._UNSAFE_CHARS_RE.search(ch) is None, (
            f"Widened _UNSAFE_CHARS_RE must NOT match safe character "
            f"{ch!r} ({hex(ord(ch))})"
        )
