"""Sentinel PoC: ``scripts/generate_sitemap.py:_UNSAFE_URL_CHARS`` was the
deferred sibling of the 2026-05-11 *Tag-Character / Variation-Selector
Drift* round (``.jules/sentinel.md`` line 1720).

The Round-11 journal entry widened every canonical-sanitiser regex in
lockstep to cover the Unicode Tag block (U+E0000..U+E007F), the BMP
Variation Selectors (U+FE00..U+FE0F), and the supplementary Variation
Selectors (U+E0100..U+E01EF). The closing inventory enumerated:

  * ``src/utils/logging.py:_INVISIBLE_DANGEROUS_RE``
  * ``src/utils/logging.py:_CONTROL_CHARS_RE``
  * ``src/utils/http.py:_UNSAFE_URL_CHARS``       <-- widened
  * ``src/utils/text.py:_MARKDOWN_NORMALISE_UNSAFE_RE``
  * ``src/utils/stats.py:_CSV_CONTROL_CHARS_RE``
  * ``src/utils/stations_validation.py:_UNSAFE_CHARS_RE``
  * ``src/utils/serialize.py:_TROJAN_SOURCE_PRIMITIVES_RE``
  * ``src/feed/reporting.py:_CONTROL_CHARS_RE``
  * ``src/build_feed.py:_CONTROL_RE``

But the sibling ``scripts/generate_sitemap.py:_UNSAFE_URL_CHARS`` — added
by the 2026-05-10 *BiDi-Mark Drift Round 6* closing round (#1452) and
documented as a "byte-exact mirror of ``src/utils/http.py:_UNSAFE_URL_CHARS``"
— was NOT widened in the same pass. The narrow regex at
``scripts/generate_sitemap.py:50`` matches the post-Round-6 baseline but
omits the three Round-11 supplementary code-point ranges, so the
"byte-exact mirror" contract pinned by the comment quietly diverged.

The drift slipped past
``tests/test_sentinel_sitemap_unsafe_chars_canonical_drift.py`` because
that file's ``CANONICAL_DANGEROUS_CHARS`` inventory list was the
pre-Round-11 enumeration (BiDi + zero-width + ASCII control + structural
URL-injection chars only) — the inventory invariant test programmatically
asserts that the sitemap regex matches every element of the inventory
list, so adding new canonical code points without extending the inventory
list leaves the test passing while the drift exists.

Threat model
------------

``SITE_BASE_URL`` is an environment-controlled string that the
``update-cycle.yml`` workflow interpolates into:

  1. Every ``<loc>`` element of ``docs/sitemap.xml`` (committed to the
     repository and served at the public GitHub Pages URL).
  2. ``docs/robots.txt`` 's ``Sitemap:`` directive (also published).

A planted ``SITE_BASE_URL`` like
``https://forker.github.io/wien-oepnv\U000E0061\U000E0062`` carries
``TAG LATIN SMALL LETTER A`` / ``TAG LATIN SMALL LETTER B`` bytes that
are visually invisible (the canonical Tag-block use-case is encoding
text inside emoji modifier sequences) but byte-distinct from a
legitimate URL. The same shape with ``︀``..``️`` (BMP
Variation Selectors) or ``\U000E0100``..``\U000E01EF`` (supplementary
Variation Selectors) is equally invisible to a human reviewer and to
URL display-conversion in modern terminals / browsers / IDE previews.

Practical impact:

  * **Steganography / data smuggling** — a Tag-character payload encodes
    arbitrary text inside an otherwise-legitimate URL, smuggling
    forensic / exfiltration markers into the public sitemap that is
    indexed by every search engine crawling GitHub Pages.
  * **Prompt-injection smuggling** — every LLM-driven downstream
    service that consumes the sitemap (auto-summarisers, RSS-to-prompt
    pipelines, search-engine snippet generation) sees the Tag-character
    payload as part of the URL text; current LLMs decode the
    Tag-character payload as English text per the
    `ConceptOfMind/USe-r-CR <https://arxiv.org/abs/2406.16066>`_
    public exploit shape and execute the embedded instructions.
  * **Cache-key / GUID collision** — Tag-character / Variation-Selector
    bytes are byte-distinct but visually identical, so a future cache
    consumer that uses the rendered URL as a key sees a fresh entry
    for every variation-selector permutation.

Defence shape
-------------

The drift exists at the *first layer* of validation in
``scripts/generate_sitemap.py:_is_valid_base_url``::

    def _is_valid_base_url(candidate: str) -> bool:
        if _UNSAFE_URL_CHARS.search(candidate):
            return False
        return validate_public_feed_url(candidate, check_dns=False) is not None

The *second layer* (``validate_public_feed_url`` -> ``validate_http_url``)
uses the canonical ``src/utils/http.py:_UNSAFE_URL_CHARS`` which already
matches the Round-11 additions, so a candidate carrying Tag-character or
Variation-Selector bytes is currently rejected at the second layer. But
the prevention rule from Round 6 (``.jules/sentinel.md`` line 8079)
explicitly named the structural risk:

  > A future PR that adds a callsite of ``_UNSAFE_URL_CHARS`` in
  > ``scripts/generate_sitemap.py`` without the second-layer gate would
  > re-enable the BiDi/zero-width issue.

The same prevention rule applies to the Round-11 supplementary ranges.
Widening the narrow regex closes the structural risk proactively and
restores the "byte-exact mirror" contract that the source-file comment
already advertises.

Inventory invariant
-------------------

This module pins the post-Round-11 canonical-set coverage invariant for
the ``_UNSAFE_URL_CHARS`` regex in ``scripts/generate_sitemap.py``. The
sister inventory test extension in
``tests/test_sentinel_sitemap_unsafe_chars_canonical_drift.py`` is
updated in the same fix-PR so any future widening of the canonical
floor fires both tests until every sibling regex is widened too.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _scripts_path_bootstrap() -> None:
    """Ensure ``scripts/`` is importable for the duration of the test
    module."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))


# Code-point inventory from the 2026-05-11 *Tag-Character /
# Variation-Selector Drift* round. Every entry is part of the canonical
# ``_INVISIBLE_DANGEROUS_RE`` union widened in lockstep across every
# sanitiser site EXCEPT ``scripts/generate_sitemap.py:_UNSAFE_URL_CHARS``.
TAG_AND_VARIATION_SELECTOR_CHARS: tuple[tuple[str, str], ...] = (
    # BMP Variation Selectors (U+FE00..U+FE0F)
    ("︀", "VS1 BMP (Variation Selector 1)"),
    ("︁", "VS2 BMP"),
    ("︇", "VS8 BMP"),
    ("︎", "VS15 BMP (text-style emoji selector)"),
    ("️", "VS16 BMP (emoji-style selector)"),
    # Unicode Tag block (U+E0000..U+E007F)
    ("\U000e0000", "TAG SPACE / language tag"),
    ("\U000e0001", "LANGUAGE TAG"),
    ("\U000e0020", "TAG SPACE"),
    ("\U000e0041", "TAG LATIN CAPITAL LETTER A"),
    ("\U000e0061", "TAG LATIN SMALL LETTER A"),
    ("\U000e007f", "CANCEL TAG"),
    # Supplementary Variation Selectors (U+E0100..U+E01EF)
    ("\U000e0100", "VS17 (supplementary)"),
    ("\U000e0101", "VS18"),
    ("\U000e0150", "VS80"),
    ("\U000e01ef", "VS256 (supplementary)"),
)


# ---------------------------------------------------------------------------
# (1) Per-code-point coverage — the narrow regex post-fix must match every
#     code point in the Round-11 Tag-character + Variation-Selector
#     inventory.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code_point,label", TAG_AND_VARIATION_SELECTOR_CHARS)
def test_sitemap_unsafe_url_chars_matches_tag_and_variation_selectors(
    code_point: str, label: str
) -> None:
    """Pre-fix: the narrow regex omits the Round-11 supplementary
    ranges (``\\ufe00-\\ufe0f``, ``\\U000e0000-\\U000e007f``,
    ``\\U000e0100-\\U000e01ef``) — every Tag-character /
    Variation-Selector code point slips past the first-layer gate.
    Post-fix: the regex matches the full canonical post-Round-11 set so
    a future caller bypassing the second-layer ``validate_public_feed_url``
    gate cannot re-enable the steganography / prompt-injection /
    cache-key-collision primitives documented in the Round-11 prevention
    rule.

    Closes the bucket-(b) deferred sibling of the 2026-05-11
    Tag-Character / Variation-Selector Drift round.
    """
    from scripts import generate_sitemap

    assert generate_sitemap._UNSAFE_URL_CHARS.search(code_point) is not None, (
        f"sitemap _UNSAFE_URL_CHARS must match {label} "
        f"(U+{ord(code_point):05X}); narrow regex is the documented "
        f"deferred sibling and post-fix MUST match the post-Round-11 "
        f"canonical src/utils/http.py:_UNSAFE_URL_CHARS set."
    )


# ---------------------------------------------------------------------------
# (2) End-to-end via ``_is_valid_base_url`` — Tag-character and
#     Variation-Selector URLs are rejected at the FIRST gate post-fix
#     (the canonical-second-layer check no longer has to catch them,
#     eliminating the "future PR removes second layer" regression risk).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "candidate,label",
    [
        (
            "https://forker.github.io/wien-oepnv\U000e0061\U000e0062",
            "TAG LATIN SMALL LETTER A/B suffix",
        ),
        (
            "https://forker.github.io/wien-oepnv\U000e007f",
            "CANCEL TAG suffix",
        ),
        (
            "https://forker.github.io/wien-oepnv️",
            "VS16 BMP suffix",
        ),
        (
            "https://forker.github.io/wien-oepnv\U000e0100",
            "VS17 supplementary suffix",
        ),
        (
            "https://forker.github.io︀/wien-oepnv",
            "VS1 BMP infix",
        ),
    ],
)
def test_is_valid_base_url_rejects_tag_and_variation_selector_chars(
    candidate: str, label: str
) -> None:
    """Pre-fix: every candidate above passed the narrow first gate
    (because ``\\ufe00-\\ufe0f``, ``\\U000e0000-\\U000e007f``, and
    ``\\U000e0100-\\U000e01ef`` were absent from the regex) and was
    caught only by the canonical second gate inside
    ``validate_public_feed_url``. Post-fix: rejected at the first gate,
    defending against a future PR that removes / refactors the
    second-layer call.
    """
    from scripts import generate_sitemap

    assert generate_sitemap._is_valid_base_url(candidate) is False, (
        f"_is_valid_base_url must reject candidate carrying {label}: "
        f"{candidate!r}"
    )


# ---------------------------------------------------------------------------
# (3) Inventory invariant — the sitemap regex covers every Tag-character
#     / Variation-Selector code point in the canonical
#     ``src/utils/http.py:_UNSAFE_URL_CHARS`` floor. A future widening of
#     the canonical floor (e.g. a Unicode 17 invisible-character block)
#     fails this test until the sibling is widened too.
# ---------------------------------------------------------------------------


def test_sitemap_unsafe_url_chars_covers_canonical_tag_chars_inventory() -> None:
    """Pin the post-Round-11 canonical-set coverage invariant
    programmatically so a future widening of
    ``src/utils/http.py:_UNSAFE_URL_CHARS`` that doesn't widen this
    sibling fails at PR-review time.

    Mirrors the inventory-test shape established by
    ``tests/test_sentinel_sitemap_unsafe_chars_canonical_drift.py``
    extended to the Round-11 supplementary code-point ranges. The
    parent test's ``CANONICAL_DANGEROUS_CHARS`` list is extended in the
    same fix-PR so the two tests share a single inventory invariant
    against future drift.
    """
    from scripts import generate_sitemap
    from src.utils import http as canonical_http

    for code_point, label in TAG_AND_VARIATION_SELECTOR_CHARS:
        assert canonical_http._UNSAFE_URL_CHARS.search(code_point) is not None, (
            f"Canonical _UNSAFE_URL_CHARS does not match {label} "
            f"(U+{ord(code_point):05X}); the inventory list in this "
            f"test is out of date."
        )
        assert generate_sitemap._UNSAFE_URL_CHARS.search(code_point) is not None, (
            f"sitemap _UNSAFE_URL_CHARS lacks {label} "
            f"(U+{ord(code_point):05X}); widen the regex to mirror "
            f"src/utils/http.py:_UNSAFE_URL_CHARS (post-Round-11 floor)."
        )


# ---------------------------------------------------------------------------
# (4) Regression — legitimate GitHub-hosted URLs continue to pass.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "candidate",
    [
        "https://forker.github.io/wien-oepnv",
        "https://origamihase.github.io/wien-oepnv",
        "https://github.com/Origamihase/wien-oepnv",
        "https://example.github.io/repo",
    ],
)
def test_is_valid_base_url_accepts_legitimate_github_urls_post_fix(
    candidate: str,
) -> None:
    """Regression: widening the narrow regex MUST NOT break legitimate
    GitHub-hosted URLs. Pre- and post-fix behaviour are identical for
    the legitimate set."""
    from scripts import generate_sitemap

    assert generate_sitemap._is_valid_base_url(candidate) is True, (
        f"_is_valid_base_url must accept legitimate URL {candidate!r} "
        f"both pre- and post-fix"
    )
