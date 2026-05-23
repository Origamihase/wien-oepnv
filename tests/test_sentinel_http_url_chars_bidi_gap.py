"""Sentinel PoC: ``_UNSAFE_URL_CHARS`` in ``src/utils/http.py`` is
narrower than the canonical ``_INVISIBLE_DANGEROUS_RE`` from
``src/utils/logging.py``.

The 2026-05-09 BiDi-Mark Drift Round 3 entry (audit)
("Two-Site Drift Closure: OSMOverpassConfig Host-Only Validation +
``_UNSAFE_CHARS_RE`` BiDi/Zero-Width Gap") closed the validator regex
in ``src/utils/stations_validation.py`` and explicitly named
``_UNSAFE_URL_CHARS`` in ``src/utils/http.py`` as a sibling drift
candidate that the round did NOT close::

    Whenever a defence regex grows to cover a new code point, audit
    every sibling regex in the project (``stations_validation.
    _UNSAFE_CHARS_RE``, ``_UNSAFE_URL_CHARS`` in ``http.py``,
    station-name validators in provider modules) and either widen
    them to match or document the divergence with an explicit
    deferral note.

This file is the Round 4 PoC: the same companion-regex sync drift
recurred in the URL validation boundary that gates every URL flowing
into the published RSS feed (item ``link``), the GitHub-Issue
auto-submission API URL, the sitemap-redirect base URL, and every
``request_safe`` / ``fetch_content_safe`` outbound HTTP call.

Pre-fix character class (``src/utils/http.py:89``)::

    [\\s\\x00-\\x1f\\x7f<>\\"\\\\^`{|}]

The set covers ASCII whitespace (``\\s`` — including ``\\t``/``\\n``/
``\\r`` and the Unicode line/paragraph separators U+2028/U+2029),
ASCII C0 controls plus DEL, plus the structural URL-injection
characters ``< > " \\\\ ^ \\` { | }``. It explicitly **DOES NOT**
cover the BiDi / zero-width / line-terminator family that the
canonical ``src/utils/logging.py:_INVISIBLE_DANGEROUS_RE`` strips:

  * ``\\u061c`` — ARABIC LETTER MARK (ALM, post-Unicode-6.3 BiDi
    control). Same Trojan-Source primitive as the LRE/RLE family.
  * ``\\u200b-\\u200f`` — ZWSP / ZWNJ / ZWJ / **LRM** / **RLM**.
    The LRM/RLM marks invert displayed text the same way the
    already-covered-by-``\\s`` ``\\u2028``/``\\u2029`` separators
    can split a log line, but are visually invisible — a planted
    URL with LRM looks identical to a benign one in any feed
    reader / GitHub UI / IDE that does not render the format
    control characters.
  * ``\\u202a-\\u202e`` — LRE / RLE / PDF / LRO / **RLO**. The RLO
    primitive is the canonical CVE-2021-42574 *Trojan Source URL*
    payload: a feed item ``link`` like ``https://safe.example.com
    \\u202E/path/evil.exe`` is rendered by Unicode-aware feed
    readers with the post-RLO segment reversed visually, so the
    URL the user *sees* differs from the URL the browser actually
    follows when clicked. Phishing primitive in a public artefact
    served from ``https://origamihase.github.io/wien-oepnv/feed.xml``.
  * ``\\u2066-\\u2069`` — LRI / RLI / FSI / PDI BiDi isolates
    (CVE-2021-42574 second half).
  * ``\\ufeff`` — BYTE ORDER MARK / ZWNBSP. A planted URL with a
    leading BOM looks identical to the canonical URL but has
    different bytes; cache-key collisions and equality checks
    silently disagree.

The canonical sanitiser ``src/utils/logging.py:_INVISIBLE_DANGEROUS_RE``
covers the full union (ALM + ZWSP/ZWNJ/ZWJ + LRM/RLM + LRE/RLE/PDF/
LRO/RLO + LRI/RLI/FSI/PDI + BOM + line/paragraph separators). The fix
in this PR widens ``_UNSAFE_URL_CHARS`` to cover the same set so a
URL with any of the missing code points is rejected at validation
time, before it reaches the published feed and operator-facing log
lines.

Threat model
------------
A compromised upstream / DNS-hijack / MITM that returns a feed item
with a planted ``link`` field carrying RLO::

    {"title": "U6: Verspätung", "link": "https://safe.example.com\\u202e/path/evil"}

The provider stores the item in the cache JSON. ``build_feed.
_format_item_content`` (``src/build_feed.py:1692``) calls
``validate_http_url(link, check_dns=False)`` to gate the link before
emitting it into the RSS ``<link>`` element. Pre-fix the validator
returns the URL unchanged — every guard inside ``validate_http_url``
(scheme, port, IDNA NFKC, SSRF) passes because the BiDi mark is in
the path, not the structural-URL components. The link lands in
``docs/feed.xml`` verbatim::

    <link>https://safe.example.com\\u202e/path/evil</link>

ElementTree XML serialisation does NOT escape U+202E (it is a valid
Unicode character, not an XML metacharacter). Subscribers reading
the feed in a Unicode-aware reader see the post-RLO segment reversed
in the rendered URL, while the actual link target retains the bytes —
a textbook Trojan-Source URL phishing primitive in a public artefact
served from the project's GitHub Pages site.

Companion fix
-------------
This file pins the invariant in three layers:

  1. **Per-code-point PoC tests** that exercise ``_UNSAFE_URL_CHARS``
     and ``validate_http_url`` / ``validate_public_feed_url`` with
     each of the missing characters and assert each is rejected
     post-fix.
  2. **Inventory test** asserting every code point matched by the
     canonical ``_INVISIBLE_DANGEROUS_RE`` is also matched by
     ``_UNSAFE_URL_CHARS``. A regression here means the two regexes
     have drifted apart again.
  3. **Coverage-preserving regression** asserting every character
     the pre-fix regex caught (ASCII whitespace, C0 controls, DEL,
     structural URL-injection chars) still matches post-fix; the
     widening must be additive.
"""

from __future__ import annotations

import pytest

from src.utils import http as canonical_http
from src.utils import logging as canonical_logging


# Code points that ``_INVISIBLE_DANGEROUS_RE`` covers but pre-fix
# ``_UNSAFE_URL_CHARS`` did not. Each is a documented BiDi /
# zero-width / Trojan-Source primitive. `` `` and `` `` are
# omitted because the pre-fix regex already caught them via ``\s``.
_MISSING_CODE_POINTS: tuple[tuple[str, str], ...] = (
    ("؜", "ARABIC LETTER MARK (ALM)"),
    ("​", "ZERO WIDTH SPACE (ZWSP)"),
    ("‌", "ZERO WIDTH NON-JOINER (ZWNJ)"),
    ("‍", "ZERO WIDTH JOINER (ZWJ)"),
    ("‎", "LEFT-TO-RIGHT MARK (LRM)"),
    ("‏", "RIGHT-TO-LEFT MARK (RLM)"),
    ("‪", "LEFT-TO-RIGHT EMBEDDING (LRE)"),
    ("‫", "RIGHT-TO-LEFT EMBEDDING (RLE)"),
    ("‬", "POP DIRECTIONAL FORMATTING (PDF)"),
    ("‭", "LEFT-TO-RIGHT OVERRIDE (LRO)"),
    ("‮", "RIGHT-TO-LEFT OVERRIDE (RLO)"),
    ("⁦", "LEFT-TO-RIGHT ISOLATE (LRI)"),
    ("⁧", "RIGHT-TO-LEFT ISOLATE (RLI)"),
    ("⁨", "FIRST STRONG ISOLATE (FSI)"),
    ("⁩", "POP DIRECTIONAL ISOLATE (PDI)"),
    ("﻿", "BYTE ORDER MARK (BOM)"),
)


@pytest.mark.parametrize(
    "code_point,label",
    _MISSING_CODE_POINTS,
    ids=[label for _, label in _MISSING_CODE_POINTS],
)
def test_unsafe_url_chars_regex_matches_missing_code_point(
    code_point: str, label: str
) -> None:
    """Pre-fix: ``_UNSAFE_URL_CHARS`` did not match ``code_point`` so a
    URL containing it slipped past ``validate_http_url``. Post-fix:
    the regex matches the code point and the validator rejects the
    URL.
    """
    assert canonical_http._UNSAFE_URL_CHARS.search(code_point) is not None, (
        f"_UNSAFE_URL_CHARS must match {label} ({hex(ord(code_point))}); "
        "see the audit (BiDi-Mark Drift Round 3) for the full "
        "list of code points the URL validator must reject. The Round 3 "
        "entry explicitly named ``_UNSAFE_URL_CHARS`` as a sibling drift "
        "candidate that did not get closed in the same round."
    )


@pytest.mark.parametrize(
    "code_point,label",
    _MISSING_CODE_POINTS,
    ids=[label for _, label in _MISSING_CODE_POINTS],
)
def test_validate_http_url_rejects_planted_url_with_missing_code_point(
    code_point: str, label: str
) -> None:
    """End-to-end PoC: a URL whose path / query carries
    ``code_point`` must be rejected by ``validate_http_url``.

    Pre-fix the validator returned the URL unchanged because the
    BiDi mark is in the path (not the structural-URL components),
    every other guard (scheme, port, IDNA NFKC, SSRF, userinfo)
    passed, and the URL lands in the public RSS feed verbatim.
    """
    poisoned = f"https://safe.example.com{code_point}/path/evil"
    result = canonical_http.validate_http_url(poisoned, check_dns=False)
    assert result is None, (
        f"validate_http_url must reject a URL containing {label} "
        f"({hex(ord(code_point))}); pre-fix it returned {result!r} "
        "and the URL flowed into the public feed verbatim. The post-"
        "fix shape is: any of the canonical _INVISIBLE_DANGEROUS_RE "
        "code points in the URL is a hard-reject, regardless of "
        "where in the URL it appears (path / query / fragment / "
        "host segment)."
    )


@pytest.mark.parametrize(
    "code_point,label",
    _MISSING_CODE_POINTS,
    ids=[label for _, label in _MISSING_CODE_POINTS],
)
def test_validate_public_feed_url_rejects_planted_url_with_missing_code_point(
    code_point: str, label: str
) -> None:
    """End-to-end PoC: ``validate_public_feed_url`` must inherit the
    rejection from ``validate_http_url``.

    The public-feed validator gates the published feed-link / sitemap
    base URL / atom self-link, so a planted code point here
    propagates into the public ``feed.xml`` ``<link>`` /
    ``<atom:link rel='self'>`` elements and into ``robots.txt``'s
    ``Sitemap:`` directive.
    """
    # Use a host on the GitHub-Pages allow-list so the public-feed
    # validator does not reject the URL on host grounds — the rejection
    # we want to assert is the new BiDi-mark check, not the existing
    # host-allow-list check. The chosen host was vetted for the public
    # feed in the 2026-05-09 "Public Feed URL Allow-List Drift" round.
    poisoned = f"https://origamihase.github.io/wien-oepnv{code_point}/feed.xml"
    result = canonical_http.validate_public_feed_url(poisoned, check_dns=False)
    assert result is None, (
        f"validate_public_feed_url must reject a URL containing "
        f"{label} ({hex(ord(code_point))}); pre-fix it returned "
        f"{result!r} and the URL flowed into the public feed and "
        "the published sitemap verbatim. The public-feed validator "
        "delegates the BiDi-mark check to validate_http_url, which "
        "in turn delegates to _UNSAFE_URL_CHARS — the regex sync "
        "fix at the lower layer closes both surfaces in one cut."
    )


def test_unsafe_url_chars_regex_covers_canonical_invisible_dangerous_set() -> None:
    """Inventory invariant: every character that
    :data:`src.utils.logging._INVISIBLE_DANGEROUS_RE` matches MUST
    also match :data:`src.utils.http._UNSAFE_URL_CHARS`.

    A regression here means the two regexes have drifted apart again
    — either the URL validator was narrowed (drift) or the canonical
    log sanitiser was widened without a matching update at the URL
    boundary. Both shapes leak a planted URL carrying the newly-listed
    code point past the validator and into the published feed / GitHub
    Issue body / sitemap / outbound HTTP requests.

    Mirrors the
    ``test_unsafe_chars_regex_covers_canonical_invisible_dangerous_set``
    invariant added by BiDi-Mark Drift Round 3 for the
    ``stations_validation._UNSAFE_CHARS_RE`` regex. Together the two
    inventory tests programmatically pin the companion-regex sync
    rule for both validation boundaries — any future widening of
    ``_INVISIBLE_DANGEROUS_RE`` (e.g. a new Unicode 16 BiDi format
    control) fails both tests until both validators are widened too.
    """
    canonical = canonical_logging._INVISIBLE_DANGEROUS_RE
    validator = canonical_http._UNSAFE_URL_CHARS

    # Materialise every code point the canonical regex matches and
    # assert the URL validator regex matches the same set.
    canonical_code_points: list[int] = []
    for cp in range(0x110000):  # full Unicode BMP + supplementary planes
        if canonical.fullmatch(chr(cp)):
            canonical_code_points.append(cp)

    # Sanity: the canonical regex covers a non-trivial set.
    assert canonical_code_points, (
        "Canonical _INVISIBLE_DANGEROUS_RE matches nothing — likely a "
        "regression in the canonical regex itself"
    )

    missing: list[int] = []
    for cp in canonical_code_points:
        if not validator.fullmatch(chr(cp)):
            missing.append(cp)

    assert not missing, (
        "_UNSAFE_URL_CHARS is narrower than _INVISIBLE_DANGEROUS_RE; "
        f"missing {len(missing)} code point(s): "
        + ", ".join(f"U+{cp:04X}" for cp in missing[:20])
        + (" …" if len(missing) > 20 else "")
        + "\nThe two regexes must stay in sync: any code point covered "
        "by the canonical log sanitiser must also be flagged by the "
        "URL validator. See the audit (BiDi-Mark Drift "
        "Round 3, sibling drift candidate enumeration) for the "
        "closing rule."
    )


def test_unsafe_url_chars_regex_preserves_existing_coverage() -> None:
    """Regression: every character ``_UNSAFE_URL_CHARS`` matched
    pre-fix must still match post-fix. The widening MUST be additive.

    Covers ASCII whitespace (``\\t``/``\\n``/``\\r``/space + the
    Unicode separators caught by ``\\s``), C0 controls + DEL, and the
    structural URL-injection chars ``< > " \\\\ ^ \\` { | }``.
    """
    pre_fix_must_match = (
        " ",   # space
        "\t",  # TAB
        "\n",  # LF
        "\r",  # CR
        "\x00",
        "\x01",
        "\x07",
        "\x0b",
        "\x0c",
        "\x0e",
        "\x1f",
        "\x7f",  # DEL
        "<",
        ">",
        '"',
        "\\",
        "^",
        "`",
        "{",
        "|",
        "}",
        " ",  # LINE SEPARATOR (already caught by \s)
        " ",  # PARAGRAPH SEPARATOR (already caught by \s)
    )
    for cp in pre_fix_must_match:
        assert canonical_http._UNSAFE_URL_CHARS.search(cp) is not None, (
            f"Existing coverage must be preserved: {hex(ord(cp))} "
            "must still match _UNSAFE_URL_CHARS after the widening."
        )


def test_unsafe_url_chars_regex_does_not_match_safe_url_chars() -> None:
    """Regression: ASCII letters / digits / common URL punctuation
    must NOT match the widened regex. Legitimate URLs carry these
    characters (``/`` path separator, ``?`` query separator, ``&``
    parameter separator, ``=`` key/value separator, ``#`` fragment,
    ``-``/``_``/``.``/``~`` unreserved, ``%``/``+`` percent-encoding /
    plus-as-space, ``@`` for userinfo separator already rejected by
    a separate guard, ``[``/``]`` for IPv6 literals, ``:`` for port
    / scheme separator). The fix must not be a super-set that turns
    every legitimate URL into an invalid URL.
    """
    safe_chars = (
        "https://example.com:443/path/to/resource"
        "?key=value&foo=bar+baz#fragment-id_AZaz09.~/"
        "-_.~"  # unreserved punctuation
        "%21%2A%28%29"  # percent-encoded
    )
    for ch in safe_chars:
        assert canonical_http._UNSAFE_URL_CHARS.search(ch) is None, (
            f"Widened _UNSAFE_URL_CHARS must NOT match safe URL "
            f"character {ch!r} ({hex(ord(ch))})"
        )


def test_validate_http_url_accepts_clean_url_post_fix() -> None:
    """Regression: a clean ``https://`` URL with no BiDi marks must
    still pass ``validate_http_url`` post-fix. Sanity check that the
    widening did not over-reach.
    """
    clean = "https://safe.example.com/path/to/resource?key=value"
    result = canonical_http.validate_http_url(clean, check_dns=False)
    assert result == clean, (
        f"validate_http_url regression: clean URL {clean!r} should "
        f"pass post-fix but returned {result!r}"
    )
