"""Sentinel PoC: ``src/feed/reporting.py:_CONTROL_CHARS_RE`` is a
*name-collision sibling* of ``src/utils/logging.py:_CONTROL_CHARS_RE``
that drifted narrower than the canonical floor.

Threat model — Log-Injection Drift Round 5
------------------------------------------

Round 4 (PR #1422, journaled 2026-05-10) widened
``src/utils/logging.py:_INVISIBLE_DANGEROUS_RE`` to include
``\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f`` (the ASCII C0 set minus
readable whitespace). The closing-checklist programmatically pinned
the canonical floor across four canonical sibling regexes:

* ``src/utils/logging.py:_INVISIBLE_DANGEROUS_RE`` — always-strip floor
* ``src/utils/text.py:_MARKDOWN_NORMALISE_UNSAFE_RE``
* ``src/utils/stats.py:_CSV_CONTROL_CHARS_RE``
* ``src/build_feed.py:_CONTROL_RE`` (the RSS-XML writer)

But ``src/feed/reporting.py`` declares its OWN ``_CONTROL_CHARS_RE``
at module level — a name-collision sibling of the canonical
``src/utils/logging.py:_CONTROL_CHARS_RE`` — narrowed to::

    _CONTROL_CHARS_RE = re.compile(r"[\\x00-\\x1f\\x7f]")

while ``src/utils/logging.py:_CONTROL_CHARS_RE`` covers the full
canonical-floor superset::

    [\\x00-\\x1f\\x7f-\\x9f\\u061c\\u200b-\\u200f\\u2028-\\u202e\\u2066-\\u2069\\ufeff]

Currently the reporting regex's drift is **transitively closed** by
the immediate ``clean_message`` delegation in ``_sanitize_log_detail``
(line 100-105)::

    def _sanitize_log_detail(detail: str) -> str:
        if not detail:
            return ""
        sanitized = _CONTROL_CHARS_RE.sub(" ", detail)
        return clean_message(sanitized)

``clean_message`` calls ``sanitize_log_message(strip_control_chars=False)``
which routes through the canonical ``_INVISIBLE_DANGEROUS_RE``
always-strip floor — so C1 / BiDi / zero-width chars that the narrow
first-layer regex misses ARE caught at the second layer.

But the bucket-(b) deferred status is fragile:

* A future PR that adds a NEW caller of ``_CONTROL_CHARS_RE`` in this
  module without delegating to ``clean_message`` would re-open the
  C1 / BiDi / zero-width hole. Same shape as the 2026-05-10 *BiDi-Mark
  Drift Round 7* round (sitemap deferred bucket-(b) sibling) which
  closed proactively before a future refactor re-enabled the issue.
* A future refactor that drops the ``clean_message`` second layer
  (intentionally or accidentally) leaves only the narrow first-layer
  regex defending the GitHub-Issue-body / feed-health.md sinks against
  C1 / BiDi / zero-width primitives.
* The name-collision with ``src/utils/logging.py:_CONTROL_CHARS_RE``
  invites copy-paste mistakes — a contributor reading ``_CONTROL_CHARS_RE``
  in feed/reporting.py and assuming it has the same coverage as the
  canonical sibling would silently re-introduce the gap.

**Fix shape:** Widen ``src/feed/reporting.py:_CONTROL_CHARS_RE`` to
byte-exact mirror ``src/utils/logging.py:_CONTROL_CHARS_RE``. The
widening is **additive-only**: every code point the pre-fix regex
matched (full ASCII C0 + DEL) still matches post-fix; the only delta
is the addition of the 50 non-ASCII code points (32 C1 controls +
18 BiDi / zero-width / LSEP / PSEP / BOM / ALM characters).

The replacement semantics (``_CONTROL_CHARS_RE.sub(" ", detail)`` —
replace with SPACE) are preserved: the post-fix regex matches more
characters but still replaces them with SPACE, preserving token
boundaries; the downstream ``clean_message`` whitespace-collapse
step folds multiple spaces back to one. End-to-end behaviour for
existing callers is observably equivalent.

Inventory invariant
-------------------

The post-fix ``_CONTROL_CHARS_RE`` in ``src/feed/reporting.py`` MUST
match the canonical ``src/utils/logging.py:_CONTROL_CHARS_RE`` byte-
exact. The closing-checklist for the *Log-Injection Drift* family
is amended to walk every ``_CONTROL_CHARS_RE`` symbol across the
``src/`` tree and assert canonical-floor agreement — same shape as
the inventory tests for ``_CSV_CONTROL_CHARS_RE`` /
``_MARKDOWN_NORMALISE_UNSAFE_RE`` / ``_CONTROL_RE`` already in place
across the four-round drift family.

Severity
--------

LOW — defence-in-depth. No current vulnerability surface (the
``clean_message`` second layer covers the gap) but a structural
drift candidate with a documented future-regression shape. Closes
the last name-collision sibling of the canonical control-char
regex family.
"""
from __future__ import annotations

import pytest

from src.feed.reporting import _CONTROL_CHARS_RE, _sanitize_log_detail
from src.utils import logging as canonical_logging


# Canonical sibling regex this round MUST byte-exact mirror.
CANONICAL_LOGGING_RE = canonical_logging._CONTROL_CHARS_RE


# Canonical floor character set (post-Round 4 widened
# ``_INVISIBLE_DANGEROUS_RE`` plus readable whitespace TAB/LF/CR
# which ``_CONTROL_CHARS_RE`` covers since it's the
# strip_control_chars=True full set).
CANONICAL_FLOOR_CHARS: tuple[tuple[str, str], ...] = (
    # ASCII C0 (full range incl. readable whitespace TAB/LF/CR)
    ("\x00", "U+0000 NUL"),
    ("\x01", "U+0001 SOH"),
    ("\x07", "U+0007 BEL"),
    ("\x08", "U+0008 BS"),
    ("\x09", "U+0009 TAB (readable, kept by _CONTROL_CHARS_RE)"),
    ("\x0a", "U+000A LF (readable, kept by _CONTROL_CHARS_RE)"),
    ("\x0b", "U+000B VT"),
    ("\x0c", "U+000C FF"),
    ("\x0d", "U+000D CR (readable, kept by _CONTROL_CHARS_RE)"),
    ("\x0e", "U+000E SO"),
    ("\x1b", "U+001B ESC"),
    ("\x1f", "U+001F US"),
    # DEL
    ("\x7f", "U+007F DEL"),
    # 8-bit C1 (Round 3 widening axis)
    ("\x80", "U+0080 PAD"),
    ("\x9b", "U+009B CSI (8-bit ESC [)"),
    ("\x9d", "U+009D OSC (8-bit ESC ])"),
    ("\x9f", "U+009F APC (8-bit ESC _)"),
    # BiDi / zero-width / line-terminator / BOM (Round 2 widening axis)
    ("؜", "U+061C ALM (Arabic Letter Mark)"),
    ("​", "U+200B ZWSP (Zero-Width Space)"),
    ("‌", "U+200C ZWNJ"),
    ("‍", "U+200D ZWJ"),
    ("‎", "U+200E LRM"),
    ("‏", "U+200F RLM"),
    (" ", "U+2028 LSEP (Line Separator)"),
    (" ", "U+2029 PSEP (Paragraph Separator)"),
    ("‪", "U+202A LRE"),
    ("‫", "U+202B RLE"),
    ("‬", "U+202C PDF"),
    ("‭", "U+202D LRO"),
    ("‮", "U+202E RLO (Trojan Source)"),
    ("⁦", "U+2066 LRI"),
    ("⁧", "U+2067 RLI"),
    ("⁨", "U+2068 FSI"),
    ("⁩", "U+2069 PDI"),
    ("﻿", "U+FEFF BOM / ZWNBSP"),
)


# ---------------------------------------------------------------------------
# (1) Per-code-point coverage — the post-fix regex MUST match every
#     code point in the canonical floor.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("char,name", CANONICAL_FLOOR_CHARS)
def test_reporting_control_chars_re_matches_canonical_floor(
    char: str, name: str
) -> None:
    """Pre-fix: ``src/feed/reporting.py:_CONTROL_CHARS_RE`` covers
    only ``[\\x00-\\x1f\\x7f]`` — the C1 / BiDi / zero-width /
    LSEP/PSEP / BOM family slips past the narrow first layer (and
    relies on ``clean_message`` to catch them at the second layer).

    Post-fix the regex byte-exact mirrors
    ``src/utils/logging.py:_CONTROL_CHARS_RE`` so a future caller of
    the local symbol that bypasses ``clean_message`` (e.g. a new
    sanitiser added to this module that copies the
    ``_CONTROL_CHARS_RE.sub(...)`` pattern but forgets to delegate)
    inherits the canonical-floor defence at the first layer.

    Closes the last name-collision sibling of the canonical
    control-char regex family across the *Log-Injection Drift* rounds.
    """
    assert _CONTROL_CHARS_RE.search(char) is not None, (
        f"feed/reporting._CONTROL_CHARS_RE must match {name} "
        f"(U+{ord(char):04X}); the post-fix regex MUST byte-exact "
        f"mirror the canonical src/utils/logging.py:_CONTROL_CHARS_RE."
    )


# ---------------------------------------------------------------------------
# (2) Byte-exact match against canonical sibling.
# ---------------------------------------------------------------------------


def test_reporting_control_chars_re_byte_exact_matches_canonical_logging() -> None:
    """Post-fix invariant: every code point that
    ``src/utils/logging.py:_CONTROL_CHARS_RE`` matches MUST also
    match ``src/feed/reporting.py:_CONTROL_CHARS_RE``, and vice
    versa. The two regexes share a name across modules — they MUST
    share their character class too, or a future contributor reading
    one and assuming the other agrees silently re-introduces the gap.

    A regression here means the two regexes have drifted apart —
    either the reporting one was narrowed (drift) or the canonical
    one was widened without a matching update at the reporting
    boundary. Both shapes leak C1 / BiDi / zero-width primitives
    into the GitHub-Issue-body / feed-health.md sinks if any future
    caller of ``_CONTROL_CHARS_RE`` in feed/reporting bypasses
    ``clean_message``.
    """
    canonical_code_points = {
        cp for cp in range(0x110000) if CANONICAL_LOGGING_RE.fullmatch(chr(cp))
    }
    reporting_code_points = {
        cp for cp in range(0x110000) if _CONTROL_CHARS_RE.fullmatch(chr(cp))
    }

    assert canonical_code_points, (
        "Canonical src/utils/logging.py:_CONTROL_CHARS_RE matches nothing "
        "— likely a regression in the canonical regex itself."
    )

    only_in_canonical = canonical_code_points - reporting_code_points
    only_in_reporting = reporting_code_points - canonical_code_points

    assert not only_in_canonical, (
        f"feed/reporting._CONTROL_CHARS_RE is narrower than canonical "
        f"src/utils/logging.py:_CONTROL_CHARS_RE; missing "
        f"{len(only_in_canonical)} code point(s): "
        + ", ".join(f"U+{cp:04X}" for cp in sorted(only_in_canonical)[:20])
        + (" …" if len(only_in_canonical) > 20 else "")
    )
    assert not only_in_reporting, (
        f"feed/reporting._CONTROL_CHARS_RE matches code points NOT in "
        f"canonical src/utils/logging.py:_CONTROL_CHARS_RE; extra "
        f"{len(only_in_reporting)} code point(s): "
        + ", ".join(f"U+{cp:04X}" for cp in sorted(only_in_reporting)[:20])
    )


# ---------------------------------------------------------------------------
# (3) Inventory invariant — every code point in the canonical
#     ``_INVISIBLE_DANGEROUS_RE`` always-strip floor MUST be matched
#     by ``feed/reporting._CONTROL_CHARS_RE`` (the strip_control_chars
#     =True superset includes the always-strip floor by design).
# ---------------------------------------------------------------------------


def test_reporting_control_chars_re_covers_invisible_dangerous_floor() -> None:
    """Inventory invariant: every code point that
    ``src/utils/logging.py:_INVISIBLE_DANGEROUS_RE`` (the always-strip
    floor) matches MUST also match
    ``src/feed/reporting.py:_CONTROL_CHARS_RE`` (the strip-with-space
    layer). Mirrors the
    ``test_invisible_dangerous_re_subset_of_control_chars_re``
    invariant from the Round 3 PoC test, extended to the
    feed/reporting module.

    A regression here means a future PR widened the always-strip
    floor without widening the reporting boundary — same drift shape
    as Round 4 (which fixed the inverse: floor was narrower than
    reporting). The bidirectional invariant is now pinned.
    """
    invisible = canonical_logging._INVISIBLE_DANGEROUS_RE
    invisible_code_points = [
        cp for cp in range(0x110000) if invisible.fullmatch(chr(cp))
    ]

    missing = [
        cp for cp in invisible_code_points
        if not _CONTROL_CHARS_RE.fullmatch(chr(cp))
    ]
    assert not missing, (
        f"feed/reporting._CONTROL_CHARS_RE is narrower than "
        f"src/utils/logging.py:_INVISIBLE_DANGEROUS_RE; missing "
        f"{len(missing)} code point(s): "
        + ", ".join(f"U+{cp:04X}" for cp in missing[:20])
        + (" …" if len(missing) > 20 else "")
    )


# ---------------------------------------------------------------------------
# (4) Regression: the existing C0 + DEL coverage is preserved.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "char,name",
    [
        ("\x00", "U+0000 NUL"),
        ("\x07", "U+0007 BEL"),
        ("\x09", "U+0009 TAB"),
        ("\x0a", "U+000A LF"),
        ("\x0d", "U+000D CR"),
        ("\x1b", "U+001B ESC"),
        ("\x1f", "U+001F US"),
        ("\x7f", "U+007F DEL"),
    ],
)
def test_reporting_control_chars_re_regression_c0_del(
    char: str, name: str
) -> None:
    """Regression: every code point the pre-fix regex matched MUST
    still match post-fix. The widening is additive-only — TAB / LF /
    CR remain matched (they get replaced with space, then collapsed
    by ``clean_message``'s ``\\s+`` step) so the existing
    ``_sanitize_log_detail`` behaviour is preserved.
    """
    assert _CONTROL_CHARS_RE.search(char) is not None, (
        f"Regression: {name} ({char!r}) was matched pre-fix but is "
        f"NOT matched post-fix — additive-only contract violated."
    )


# ---------------------------------------------------------------------------
# (5) Regression: legitimate text characters are NOT matched.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "char",
    [
        "A", "z", "0", "9", " ", "ä", "ö", "ü", "ß",
        "→", "🔒", "Ω", "Ä", "É",
    ],
)
def test_reporting_control_chars_re_does_not_match_legitimate_text(
    char: str,
) -> None:
    """Regression: the widened regex MUST NOT match legitimate text
    characters. The canonical floor is invisible-control-only; every
    printable Unicode letter / digit / space / arrow / emoji passes
    through unmatched.
    """
    assert _CONTROL_CHARS_RE.search(char) is None, (
        f"Widened _CONTROL_CHARS_RE incorrectly matches legitimate "
        f"text character {char!r} (U+{ord(char):04X}); the canonical "
        f"floor is invisible-control-only."
    )


# ---------------------------------------------------------------------------
# (6) End-to-end — _sanitize_log_detail end-to-end behaviour for the
#     practical input shape.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "char,name",
    [
        ("\x00", "U+0000 NUL"),
        ("\x07", "U+0007 BEL"),
        ("\x9b", "U+009B CSI"),
        ("\x9d", "U+009D OSC"),
        ("‮", "U+202E RLO"),
        ("​", "U+200B ZWSP"),
        (" ", "U+2028 LSEP"),
        ("﻿", "U+FEFF BOM"),
    ],
)
def test_sanitize_log_detail_end_to_end_strips_canonical_floor(
    char: str, name: str
) -> None:
    """End-to-end: ``_sanitize_log_detail`` MUST strip every
    canonical-floor character. Pre-fix this test passed transitively
    via ``clean_message`` (the second-layer always-strip floor).
    Post-fix the narrow first layer also strips them (replacing with
    space), so a future refactor that drops the ``clean_message``
    delegation does NOT regress the public-artefact contract.
    """
    payload = f"prefix{char}suffix"
    sanitized = _sanitize_log_detail(payload)
    assert char not in sanitized, (
        f"{name} ({char!r}) survived _sanitize_log_detail: {sanitized!r}"
    )


# ---------------------------------------------------------------------------
# (7) Inventory invariant — the regex pattern source byte-exact
#     matches the canonical sibling. Pins the literal pattern, not
#     just observable behaviour, so a future cosmetic refactor (e.g.
#     splitting into multi-line raw strings) is forced to keep the
#     two patterns lexically identical.
# ---------------------------------------------------------------------------


def test_reporting_control_chars_re_pattern_matches_canonical_pattern() -> None:
    """Pin the regex *pattern source* byte-exact to the canonical
    sibling. A future contributor who edits one regex source MUST
    edit the other in the same PR — the lexical pin makes the
    requirement visible at PR-review time.

    Mirrors the cross-regex pattern-source pinning used by the
    closing-checklist greps documented in the audit.
    """
    assert _CONTROL_CHARS_RE.pattern == CANONICAL_LOGGING_RE.pattern, (
        "feed/reporting._CONTROL_CHARS_RE pattern drifted from "
        "src/utils/logging.py:_CONTROL_CHARS_RE pattern.\n\n"
        f"reporting:  {_CONTROL_CHARS_RE.pattern!r}\n"
        f"canonical:  {CANONICAL_LOGGING_RE.pattern!r}\n\n"
        "These two regexes share a name across modules and MUST share "
        "their pattern source byte-exact. Edit both in the same PR."
    )
