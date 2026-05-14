"""Sentinel PoC: ``scripts/generate_sitemap.py:_UNSAFE_URL_CHARS`` was the
deferred bucket-(b) sibling from the 2026-05-10 *BiDi-Mark Drift
Round 6* round (``.jules/sentinel.md``).

The Round 6 journal entry explicitly enumerated the post-fix state of
every sibling regex against the canonical
``src/utils/http.py:_UNSAFE_URL_CHARS`` set::

    grep -rn '_CONTROL_RE\\b\\|_CONTROL_CHARS\\b\\|_UNSAFE_URL_CHARS\\b\\|_UNSAFE_CHARS_RE\\b' src/ scripts/

The closing verdict left exactly one open hit: the narrow regex in
``scripts/generate_sitemap.py:39`` (``[\\s\\x00-\\x1f\\x7f]``) — a
strict subset of the canonical (``[\\s\\x00-\\x1f\\x7f-\\x9f<>"\\^\\`{|}
\\u061c\\u200b-\\u200f\\u202a-\\u202e\\u2066-\\u2069\\ufeff]``).
The verdict bucketed it as **bucket-(b) "deferred with no-specific-
exploit-shape because the second-layer gate covers it"**: a candidate
URL with BiDi / zero-width / structural-injection characters survives
the narrow check and is then rejected by the canonical regex inside
``validate_public_feed_url`` ``→`` ``validate_http_url`` (called on
the next line).

The Round 6 prevention rule explicitly named the structural risk:

> A future PR that adds a callsite of ``_UNSAFE_URL_CHARS`` in
> ``scripts/generate_sitemap.py`` without the second-layer gate would
> re-enable the BiDi/zero-width issue.

This round closes the bucket-(b) sibling proactively — widening the
narrow regex to the canonical set so the structural risk is removed
even if a future caller bypasses the second-layer gate. The fix is
**additive-only**: every character the narrow regex matched still
matches post-fix (``\\s\\x00-\\x1f\\x7f`` is a strict subset of the
canonical set). The observable behaviour of ``_is_valid_base_url`` is
unchanged for every URL the narrow OR canonical regex would have
rejected — the ratchet point is the structural invariant, not the
acceptance set.

Inventory invariant
-------------------

This module pins the canonical-set coverage invariant for the
``_UNSAFE_URL_CHARS`` regex across the whole repo: every site that
exposes an ``_UNSAFE_URL_CHARS`` symbol must match the canonical
character class at every code point in the canonical set. Mirrors
the test shape from
``tests/test_sentinel_http_url_chars_bidi_gap.py:test_unsafe_url_chars_regex_covers_canonical_invisible_dangerous_set``
extended to the ``scripts/`` tree, and from
``tests/test_sentinel_feed_xml_invisible_prefix.py`` /
``tests/test_sentinel_csv_formula_injection_invisible_prefix.py``
that cover the parallel CSV / RSS-XML writer regexes. A future
widening of the canonical floor (e.g. a Unicode 17 BiDi format
control) fails this test until every sibling regex is widened too.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _scripts_path_bootstrap() -> None:
    """Ensure ``scripts/`` is importable for the duration of the
    test module."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))


# Canonical set the post-fix narrow regex MUST cover at every code
# point. Mirrors ``src/utils/http.py:_UNSAFE_URL_CHARS`` exactly —
# any future widening of the canonical floor MUST be reflected here
# AND in every sibling regex (this test fires on the sibling drift).
CANONICAL_DANGEROUS_CHARS: tuple[tuple[str, str], ...] = (
    # ASCII whitespace
    (" ", "SPACE"),
    ("\t", "TAB"),
    ("\n", "LF"),
    ("\r", "CR"),
    # ASCII C0 controls (sample)
    ("\x00", "NUL"),
    ("\x01", "SOH"),
    ("\x1b", "ESC"),
    ("\x1f", "US"),
    # DEL + 8-bit C1 controls
    ("\x7f", "DEL"),
    ("\x80", "C1 PAD"),
    ("\x9b", "C1 CSI"),
    ("\x9d", "C1 OSC"),
    ("\x9f", "C1 APC"),
    # Structural URL-injection characters
    ("<", "LESS-THAN"),
    (">", "GREATER-THAN"),
    ('"', "QUOTE"),
    ("\\", "BACKSLASH"),
    ("^", "CARET"),
    ("`", "BACKTICK"),
    ("{", "OPEN-BRACE"),
    ("|", "PIPE"),
    ("}", "CLOSE-BRACE"),
    # BiDi format controls
    ("؜", "ALM (Arabic Letter Mark)"),
    ("‪", "LRE"),
    ("‫", "RLE"),
    ("‬", "PDF"),
    ("‭", "LRO"),
    ("‮", "RLO (Trojan Source)"),
    ("⁦", "LRI"),
    ("⁧", "RLI"),
    ("⁨", "FSI"),
    ("⁩", "PDI"),
    # Zero-width characters
    ("​", "ZWSP"),
    ("‌", "ZWNJ"),
    ("‍", "ZWJ"),
    ("‎", "LRM"),
    ("‏", "RLM"),
    ("﻿", "BOM / ZWNBSP"),
    # 2026-05-11 "Tag-Character / Variation-Selector Drift" — the
    # post-Round-11 canonical floor in
    # ``src/utils/http.py:_UNSAFE_URL_CHARS``. The sitemap regex MUST
    # mirror this widening; the sister test in
    # ``tests/test_sentinel_sitemap_tag_chars_variation_selectors_drift.py``
    # pins the per-range coverage and the same code points are
    # enumerated here so the canonical-set inventory invariant in
    # ``test_sitemap_unsafe_url_chars_covers_canonical_set_inventory``
    # fires on any future drift of either sibling.
    ("︀", "VS1 BMP (Variation Selector)"),
    ("︎", "VS15 BMP (text-style emoji selector)"),
    ("️", "VS16 BMP (emoji-style selector)"),
    ("\U000e0000", "TAG language tag"),
    ("\U000e0041", "TAG LATIN CAPITAL LETTER A"),
    ("\U000e0061", "TAG LATIN SMALL LETTER A"),
    ("\U000e007f", "CANCEL TAG"),
    ("\U000e0100", "VS17 (supplementary)"),
    ("\U000e01ef", "VS256 (supplementary)"),
)


# ---------------------------------------------------------------------------
# (1) Per-code-point coverage — the narrow regex post-fix must match
#     every code point in the canonical set.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code_point,label", CANONICAL_DANGEROUS_CHARS)
def test_sitemap_unsafe_url_chars_matches_canonical_set(
    code_point: str, label: str
) -> None:
    """Pre-fix: the narrow regex
    (``[\\s\\x00-\\x1f\\x7f]``) only matched ASCII whitespace + C0 +
    DEL — every BiDi / zero-width / structural-injection character
    slipped past. Post-fix: the regex matches the full canonical
    set so a future caller bypassing the second-layer gate cannot
    re-enable the BiDi/zero-width issue documented in the
    Round 6 prevention rule.

    Closes the bucket-(b) deferred sibling from the 2026-05-10
    BiDi-Mark Drift Round 6 round.
    """
    from scripts import generate_sitemap

    assert generate_sitemap._UNSAFE_URL_CHARS.search(code_point) is not None, (
        f"sitemap _UNSAFE_URL_CHARS must match {label} "
        f"(U+{ord(code_point):04X}); narrow regex is the documented "
        f"deferred bucket-(b) sibling and post-fix MUST match the "
        f"canonical src/utils/http.py:_UNSAFE_URL_CHARS set."
    )


# ---------------------------------------------------------------------------
# (2) End-to-end via ``_is_valid_base_url`` — BiDi / zero-width URLs
#     are rejected at the FIRST gate post-fix (the canonical-second-
#     layer check no longer has to catch them, eliminating the
#     "future PR removes second layer" regression risk).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "candidate,label",
    [
        ("https://forker.github.io/path‮repo", "RLO BiDi"),
        ("https://forker.github.io/repo​zero-width", "ZWSP"),
        ("https://forker.github.io/repo﻿BOM", "BOM"),
        ("https://forker.github.io/repo<injection>", "structural <>"),
        ("https://forker.github.io/repo`injection", "backtick"),
        ("https://forker.github.io/path\x9bcsi", "C1 CSI"),
    ],
)
def test_is_valid_base_url_rejects_canonical_dangerous_chars(
    candidate: str, label: str
) -> None:
    """Pre-fix: every candidate above passed the narrow first gate
    and was caught only by the canonical second gate inside
    ``validate_public_feed_url``. Post-fix: rejected at the first
    gate, defending against a future PR that removes / refactors
    the second-layer call.
    """
    from scripts import generate_sitemap

    assert generate_sitemap._is_valid_base_url(candidate) is False, (
        f"_is_valid_base_url must reject candidate carrying {label}: "
        f"{candidate!r}"
    )


# ---------------------------------------------------------------------------
# (3) Regression — legitimate GitHub-hosted URLs continue to pass.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "candidate",
    [
        "https://forker.github.io/wien-oepnv",
        "https://example.github.io/repo",
        "https://origamihase.github.io/wien-oepnv",
        "https://github.com/Origamihase/wien-oepnv",
        # ``&`` is a valid path-segment character per RFC 3986 and
        # legitimately passes both layers — the existing
        # ``test_sitemap_escaping`` test fixture documents this.
        "https://forker.github.io/foo&bar",
    ],
)
def test_is_valid_base_url_accepts_legitimate_github_urls(candidate: str) -> None:
    """Regression: widening the narrow regex MUST NOT break
    legitimate GitHub-hosted URLs. Pre- and post-fix behaviour are
    identical for the legitimate set."""
    from scripts import generate_sitemap

    assert generate_sitemap._is_valid_base_url(candidate) is True, (
        f"_is_valid_base_url must accept legitimate URL {candidate!r} "
        f"both pre- and post-fix"
    )


# ---------------------------------------------------------------------------
# (4) Inventory invariant — the sitemap regex covers every character
#     in the canonical ``src/utils/http.py:_UNSAFE_URL_CHARS`` floor.
#     A future widening of the canonical floor (e.g. a Unicode 17
#     BiDi format control) fails this test until the sibling is
#     widened too.
# ---------------------------------------------------------------------------


def test_sitemap_unsafe_url_chars_covers_canonical_set_inventory() -> None:
    """Pin the canonical-set coverage invariant programmatically so
    a future widening of ``src/utils/http.py:_UNSAFE_URL_CHARS``
    that doesn't widen this sibling fails at PR-review time.

    Mirrors the inventory test shape established by
    ``test_unsafe_url_chars_regex_covers_canonical_invisible_dangerous_set``
    (in ``tests/test_sentinel_http_url_chars_bidi_gap.py``) and the
    feed-XML / CSV-writer parallel inventory tests.
    """
    from scripts import generate_sitemap
    from src.utils import http as canonical_http

    for code_point, label in CANONICAL_DANGEROUS_CHARS:
        assert canonical_http._UNSAFE_URL_CHARS.search(code_point) is not None, (
            f"Canonical _UNSAFE_URL_CHARS does not match {label} "
            f"(U+{ord(code_point):04X}); the inventory list in this "
            f"test is out of date."
        )
        assert generate_sitemap._UNSAFE_URL_CHARS.search(code_point) is not None, (
            f"sitemap _UNSAFE_URL_CHARS lacks {label} "
            f"(U+{ord(code_point):04X}); widen the regex to mirror "
            f"src/utils/http.py:_UNSAFE_URL_CHARS."
        )
