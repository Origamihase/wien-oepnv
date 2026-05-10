"""Sentinel PoC: RSS-feed Trojan-Source attack via leading invisible /
BiDi / line-terminator characters at the
``src.build_feed._sanitize_text`` boundary.

The 2026-05-09 / 2026-05-10 BiDi-Mark Drift family widened every
canonical sanitiser regex (``src/utils/logging.py:_CONTROL_CHARS_RE``,
``src/utils/logging.py:_INVISIBLE_DANGEROUS_RE``,
``src/utils/text.py:_MARKDOWN_NORMALISE_UNSAFE_RE``,
``src/utils/stations_validation.py:_UNSAFE_CHARS_RE``,
``src/utils/http.py:_UNSAFE_URL_CHARS``,
``src/utils/stats.py:_CSV_CONTROL_CHARS_RE``) to mirror the
canonical Trojan-Source / line-terminator union — but the sibling
regex inside :func:`src.build_feed._sanitize_text` still covered only
the ASCII XML-control class
``[\\x00-\\x08\\x0B\\x0C\\x0E-\\x1F\\x7F]``. That regex is the LAST
sanitiser before every feed item title / description / time-line
lands inside the published RSS XML at ``docs/feed.xml`` (served from
``https://origamihase.github.io/wien-oepnv/feed.xml``), so the drift
opens a *Trojan-Source RSS* primitive on the project's public artefact.

Pre-fix character class (``src/build_feed.py:548``)::

    [\\x00-\\x08\\x0B\\x0C\\x0E-\\x1F\\x7F]

The set covers ASCII C0 controls (except TAB / LF / CR which RSS
allows verbatim) plus DEL. It explicitly **DOES NOT** cover the
BiDi / zero-width / line-terminator / C1 family that the canonical
``src/utils/logging.py:_INVISIBLE_DANGEROUS_RE`` strips:

  * ``\\u061c`` — ARABIC LETTER MARK (ALM, post-Unicode-6.3 BiDi
    control). Same Trojan-Source primitive as the LRE/RLE family.
  * ``\\u200b-\\u200f`` — ZWSP / ZWNJ / ZWJ / **LRM** / **RLM**.
    LRM/RLM invert displayed text the same way ``\\u202a-\\u202e``
    do, but are visually invisible in any feed reader / IDE that
    does not render the format control characters.
  * ``\\u2028-\\u2029`` — LINE SEPARATOR / PARAGRAPH SEPARATOR.
    Some Unicode-aware feed readers honour these as record
    terminators, splitting a single item into multiple entries
    visually downstream.
  * ``\\u202a-\\u202e`` — LRE / RLE / PDF / LRO / **RLO**. The RLO
    primitive is the canonical CVE-2021-42574 *Trojan Source*
    payload: a feed item title like ``"U6: Verspätung\\u202e\\u202e
    /path/safe"`` is rendered by Unicode-aware feed readers with
    the post-RLO segment reversed visually, so the title the user
    *sees* differs from the bytes the feed actually carries.
  * ``\\u2066-\\u2069`` — LRI / RLI / FSI / PDI BiDi isolates
    (CVE-2021-42574 second half).
  * ``\\ufeff`` — BYTE ORDER MARK / ZWNBSP. A planted title with a
    leading BOM looks identical to the canonical one but has
    different bytes; cache-key collisions and equality checks
    silently disagree.
  * ``\\x80-\\x9f`` — C1 controls (incl. U+0085 NEXT LINE which
    several SIEM splitters honour as a record terminator and
    several feed readers treat as a line break).

Threat model (highest-impact path)
----------------------------------
A compromised Wiener-Linien upstream (or MITM / DNS-hijack of the WL
endpoints, or a poisoned ``cache/wl/*.json`` / ``cache/oebb/*.json`` /
``cache/vor/*.json`` produced by a different round of supply-chain
compromise) returns an item with a planted invisible-character payload::

    {
      "title": "Linie U6: Wartung – siehe \\u202e/path/safe.html",
      "description": "Information zur Sperre …"
    }

The pipeline path:

* ``src/build_feed.py:_format_item_content`` retrieves
  ``raw_title = it.get("title")`` and routes it through
  ``_sanitize_text`` (line 1890).
* ``_sanitize_text`` returns the input unchanged because
  ``_CONTROL_RE.sub("")`` does not match U+202E (the regex covers
  ``\\x00-\\x08`` + ``\\x0B-\\x0C`` + ``\\x0E-\\x1F`` + ``\\x7F`` only).
* The result flows into ``_WHITESPACE_RE.sub(" ", title_out).strip()``
  which collapses ASCII whitespace runs but does NOT strip BiDi /
  zero-width characters (``"\\u202e".isspace()`` is ``False``;
  Python's ``\\s`` matches Unicode whitespace category but not BiDi
  format controls).
* The title is wrapped in CDATA via ``_cdata_content(title_out)``
  which only escapes ``]]>``; BiDi marks pass through verbatim.
* ``_emit_item`` constructs ``ET.SubElement(item, "title").text =
  PH_TITLE`` and the placeholder is later substituted with the
  CDATA-wrapped title in the final XML output.
* ``ET.tostring(...)`` does NOT XML-escape U+202E (it is a valid
  Unicode codepoint, not an XML metacharacter). The bytes land
  verbatim inside ``<title>`` of ``docs/feed.xml``.

The same pipeline applies to ``raw_desc`` (sanitised at line 1702
via ``html_to_text(...).strip()`` → ``_sanitize_text``) and to the
``time_line`` (line 1903) — three independent feed-output sinks
share the same drift.

Subscribers reading the feed in any Unicode-aware reader (Feedly,
NetNewsWire, Inoreader, Vivaldi RSS panel, kindle-RSS gateways,
``rsstail``, IDE-embedded readers) see the post-RLO segment reversed
in the rendered item title — a textbook Trojan-Source RSS attack on a
public artefact served from the project's GitHub Pages site.

Same bypass shape generalises across the canonical invisible-character
set:

* U+200E LRM / U+200F RLM — BiDi inversion in any reader that
  honours BiDi marks. Identical visual confusion to U+202E without
  needing a closing PDF.
* U+200B ZWSP / U+200C ZWNJ / U+200D ZWJ / U+FEFF BOM — invisible
  byte insertions create cache-key disagreements (the WL provider
  computes ``ident`` from a hash of the title; an attacker with a
  fixed ZWSP-injected title and a clean title have different
  hashes, so the dedup logic accepts both). Same shape lets a
  hostile upstream churn the dedup window indefinitely with
  visually-identical "fresh" items.
* U+2028 LINE SEPARATOR / U+2029 PARAGRAPH SEPARATOR — some feed
  readers treat these as line breaks, splitting a single item
  title into multiple visual lines (legitimate feed-reader
  behaviour: Feedly's mobile app honours ``\\u2028`` exactly as
  ``\\n``).
* U+0085 NEL — same record-terminator shape; honoured as a line
  break by several Markdown / SIEM splitters that consume the feed
  via a downstream pipeline.

Companion fix
-------------
This file pins the invariant in three layers, mirroring the
``test_sentinel_csv_formula_injection_invisible_prefix.py`` shape
established by the CSV writer round (2026-05-10):

  1. **Per-code-point regex match** — ``_CONTROL_RE`` must match each
     of the canonical invisible / BiDi code points.
  2. **Per-code-point write-path PoC** — ``_sanitize_text`` must
     strip the invisible character.
  3. **Inventory invariant** — every code point matched by the
     canonical ``src.utils.logging._INVISIBLE_DANGEROUS_RE`` must
     also match ``_CONTROL_RE``. A future widening of the canonical
     regex (e.g. a Unicode 16 BiDi format control) fails this test
     until the build-feed sanitiser is widened too.
  4. **Coverage-preserving regression** — every character the pre-
     fix ``_CONTROL_RE`` matched must still match post-fix.
  5. **Whitespace-passthrough regression** — TAB / LF / CR / SPACE
     must still NOT be stripped from the body (they are required
     for legitimate feed content; the downstream ``_WHITESPACE_RE``
     collapse normalises them anyway).
  6. **Safe-text regression** — legitimate German item titles
     (``"U6: Verspätung"``) must round-trip byte-exactly post-fix;
     the widening must not over-reach into ASCII printable text.
"""

from __future__ import annotations

import pytest

from src import build_feed
from src.utils import logging as canonical_logging


# Canonical invisible / BiDi / line-terminator code points the project's
# log sanitiser strips (see ``src/utils/logging.py:_INVISIBLE_DANGEROUS_RE``)
# plus the C1 control U+0085 (NEL) and C1 control U+0086 the Markdown
# sanitiser also covers (``src/utils/text.py:_MARKDOWN_NORMALISE_UNSAFE_RE``).
# Each is a documented invisible / Trojan-Source / record-terminator
# primitive whose presence inside an RSS item title / description would
# either confuse a Unicode-aware reader (BiDi) or smuggle a record
# terminator into a downstream consumer (line / paragraph separators).
_INVISIBLE_PREFIX_CODE_POINTS: tuple[tuple[str, str], ...] = (
    ("؜", "ARABIC LETTER MARK (ALM)"),
    ("​", "ZERO WIDTH SPACE (ZWSP)"),
    ("‌", "ZERO WIDTH NON-JOINER (ZWNJ)"),
    ("‍", "ZERO WIDTH JOINER (ZWJ)"),
    ("‎", "LEFT-TO-RIGHT MARK (LRM)"),
    ("‏", "RIGHT-TO-LEFT MARK (RLM)"),
    (" ", "LINE SEPARATOR"),
    (" ", "PARAGRAPH SEPARATOR"),
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
    ("", "C1 NEXT LINE (NEL)"),
)


# ============================================================================
# (1) Per-code-point regex match
# ============================================================================


@pytest.mark.parametrize(
    "code_point,label",
    _INVISIBLE_PREFIX_CODE_POINTS,
    ids=[label for _, label in _INVISIBLE_PREFIX_CODE_POINTS],
)
def test_control_re_matches_invisible_dangerous_code_point(
    code_point: str, label: str
) -> None:
    """Pre-fix: ``_CONTROL_RE`` only covered ASCII C0 + DEL, so a feed
    item title containing ``code_point`` slipped past ``_sanitize_text``
    and into the published ``docs/feed.xml`` verbatim. Post-fix: the
    regex matches the code point and the sanitiser strips it before the
    title reaches the XML serialiser.
    """
    assert build_feed._CONTROL_RE.search(code_point) is not None, (
        f"_CONTROL_RE must match {label} ({hex(ord(code_point))}); "
        "see .jules/sentinel.md (Feed-XML BiDi-Mark Drift) for the full "
        "list of code points the build-feed sanitiser must reject. "
        "The drift opens a Trojan-Source RSS primitive on the public "
        "feed at https://origamihase.github.io/wien-oepnv/feed.xml."
    )


# ============================================================================
# (2) Per-code-point write-path PoC — _sanitize_text strips the marker
# ============================================================================


@pytest.mark.parametrize(
    "code_point,label",
    _INVISIBLE_PREFIX_CODE_POINTS,
    ids=[label for _, label in _INVISIBLE_PREFIX_CODE_POINTS],
)
def test_sanitize_text_strips_invisible_dangerous_code_point(
    code_point: str, label: str
) -> None:
    """End-to-end PoC: ``_sanitize_text`` must strip the canonical
    invisible / BiDi / line-terminator code points from feed-item
    titles / descriptions / time-lines before they reach
    ``docs/feed.xml``.

    Pre-fix the sanitiser returned the input unchanged because the
    BiDi mark is outside the C0/DEL class, every other defence
    (whitespace-collapse, CDATA-wrap, ElementTree XML escape) passes
    the mark through, and the title lands in the public feed verbatim.
    """
    payload = f"U6: Verspätung{code_point}— heute"
    sanitised = build_feed._sanitize_text(payload)
    assert code_point not in sanitised, (
        f"_sanitize_text must strip {label} "
        f"({hex(ord(code_point))}) from feed-item titles / "
        f"descriptions; pre-fix it returned {sanitised!r} and the "
        "code point flowed into the published RSS feed verbatim. "
        "The post-fix shape is: any of the canonical "
        "_INVISIBLE_DANGEROUS_RE code points in the title / "
        "description / time-line is stripped at sanitiser entry, "
        "before the XML serialiser runs."
    )


# ============================================================================
# (3) Trojan-Source RSS title PoC — full RLO injection scenario
# ============================================================================


def test_sanitize_text_neutralises_trojan_source_rlo_payload() -> None:
    """Concrete CVE-2021-42574 shape: a planted upstream title carrying
    U+202E (RLO) renders the post-RLO segment reversed in any
    Unicode-aware feed reader. After the fix, the RLO is stripped
    so the title reads identically in any reader.
    """
    rlo = "‮"
    poisoned_title = f"Linie U6: siehe {rlo}/path/safe.html"
    sanitised = build_feed._sanitize_text(poisoned_title)
    assert rlo not in sanitised, (
        "RLO (U+202E) must be stripped from feed item titles before "
        "they reach docs/feed.xml. Pre-fix the title rendered with "
        "the post-RLO path segment reversed visually, while the "
        "underlying bytes carried the RLO mark verbatim — a textbook "
        "Trojan-Source RSS phishing primitive."
    )


def test_sanitize_text_neutralises_zwsp_dedup_bypass_payload() -> None:
    """Concrete cache-key disagreement shape: a planted upstream title
    with a leading ZWSP (U+200B) hashes to a different identity than
    the same title without the ZWSP. Pre-fix the planted variant lands
    as a fresh item every cycle, churning the dedup window; post-fix
    the ZWSP is stripped so identity-stable dedup applies.
    """
    zwsp = "​"
    poisoned_title = f"{zwsp}Linie U6: Wartung am 15. März"
    sanitised = build_feed._sanitize_text(poisoned_title)
    assert zwsp not in sanitised, (
        "ZWSP (U+200B) must be stripped from feed item titles to "
        "prevent invisible-byte-insertion dedup-bypass attacks. "
        "Pre-fix a hostile upstream could ship visually-identical "
        "items with different invisible-byte counts and bypass the "
        "ident hash dedup gate, churning the window indefinitely."
    )


# ============================================================================
# (4) Inventory invariant — every canonical _INVISIBLE_DANGEROUS_RE point
#     must also match _CONTROL_RE
# ============================================================================


def test_control_re_covers_canonical_invisible_dangerous_set() -> None:
    """Inventory invariant: every character that
    :data:`src.utils.logging._INVISIBLE_DANGEROUS_RE` matches MUST
    also match :data:`src.build_feed._CONTROL_RE`.

    A regression here means the two regexes have drifted apart again
    — either the build-feed sanitiser was narrowed (drift) or the
    canonical log sanitiser was widened without a matching update at
    the feed-output boundary. Both shapes leak a planted invisible
    character past the sanitiser and into the public RSS feed.

    Mirrors the
    ``test_csv_control_chars_regex_covers_canonical_invisible_dangerous_set``
    invariant added by the 2026-05-10 CSV writer round and the
    ``test_unsafe_url_chars_regex_covers_canonical_invisible_dangerous_set``
    invariant added by BiDi-Mark Drift Round 4. Together the three
    inventory tests programmatically pin the companion-regex sync rule
    for every defence boundary — any future widening of
    ``_INVISIBLE_DANGEROUS_RE`` (e.g. a Unicode 16 BiDi format control)
    fails all three tests until each writer's regex is widened too.
    """
    canonical = canonical_logging._INVISIBLE_DANGEROUS_RE
    sanitiser = build_feed._CONTROL_RE

    canonical_code_points: list[int] = []
    for cp in range(0x110000):
        if canonical.fullmatch(chr(cp)):
            canonical_code_points.append(cp)

    assert canonical_code_points, (
        "Canonical _INVISIBLE_DANGEROUS_RE matches nothing — likely a "
        "regression in the canonical regex itself"
    )

    missing: list[int] = []
    for cp in canonical_code_points:
        if not sanitiser.fullmatch(chr(cp)):
            missing.append(cp)

    assert not missing, (
        "_CONTROL_RE is narrower than _INVISIBLE_DANGEROUS_RE; "
        f"missing {len(missing)} code point(s): "
        + ", ".join(f"U+{cp:04X}" for cp in missing[:20])
        + (" …" if len(missing) > 20 else "")
        + "\nThe two regexes must stay in sync: any code point covered "
        "by the canonical log sanitiser must also be flagged by the "
        "build-feed sanitiser. See .jules/sentinel.md (Feed-XML BiDi-"
        "Mark Drift) for the closing rule."
    )


# ============================================================================
# (5) Coverage-preserving regression — every pre-fix match still matches
# ============================================================================


def test_control_re_preserves_existing_coverage() -> None:
    """Regression: every character ``_CONTROL_RE`` matched pre-fix must
    still match post-fix. The widening MUST be additive.

    Covers ASCII C0 controls except TAB/LF/CR (RSS allows TAB/LF/CR
    verbatim and the canonical sanitiser preserves them — only ``\\t``
    is genuinely useful in RSS body text, but legacy upstreams emit
    ``\\n`` in description bodies that downstream renderers convert
    to ``<br/>`` so we cannot silently strip it).
    """
    pre_fix_must_match = (
        "\x00",
        "\x01",
        "\x02",
        "\x03",
        "\x04",
        "\x05",
        "\x06",
        "\x07",
        "\x08",
        "\x0b",
        "\x0c",
        "\x0e",
        "\x0f",
        "\x10",
        "\x11",
        "\x12",
        "\x13",
        "\x14",
        "\x15",
        "\x16",
        "\x17",
        "\x18",
        "\x19",
        "\x1a",
        "\x1b",
        "\x1c",
        "\x1d",
        "\x1e",
        "\x1f",
        "\x7f",
    )
    for cp in pre_fix_must_match:
        assert build_feed._CONTROL_RE.search(cp) is not None, (
            f"Existing coverage must be preserved: {hex(ord(cp))} "
            "must still match _CONTROL_RE after the widening."
        )


# ============================================================================
# (6) Whitespace-passthrough regression — TAB / LF / CR / SPACE preserved
# ============================================================================


def test_control_re_does_not_match_legitimate_whitespace() -> None:
    """Regression: TAB / LF / CR / SPACE must NOT match ``_CONTROL_RE``.

    These bytes are required for legitimate RSS content. The downstream
    ``_WHITESPACE_RE.sub(" ", title_out).strip()`` collapse normalises
    them anyway, so stripping them at this earlier layer would create
    visible spacing differences (TAB is rendered as a single space; LF
    is preserved as a paragraph break in description bodies).
    """
    legitimate_whitespace = ("\t", "\n", "\r", " ")
    for ch in legitimate_whitespace:
        assert build_feed._CONTROL_RE.search(ch) is None, (
            f"_CONTROL_RE must NOT match legitimate whitespace "
            f"{ch!r} ({hex(ord(ch))}); these bytes are required "
            "for legitimate feed content and the downstream "
            "_WHITESPACE_RE collapse normalises them anyway."
        )


# ============================================================================
# (7) Safe-text regression — legitimate German titles round-trip
# ============================================================================


@pytest.mark.parametrize(
    "title",
    [
        "U6: Verspätung",
        "ÖBB: Streckeninformation",
        "Linie 41: Ersatzbus",
        "Wien Hauptbahnhof",
        "Floridsdorf — Spittelau",
        "S1: Verzögerung am Praterstern",
        "Brigittenauer Brücke gesperrt",
    ],
)
def test_sanitize_text_preserves_legitimate_german_titles(title: str) -> None:
    """Regression: legitimate German item titles must round-trip
    byte-exactly post-fix. The widening must NOT over-reach into
    ASCII printable text or German diacritics.
    """
    assert build_feed._sanitize_text(title) == title, (
        f"Legitimate title {title!r} must round-trip byte-exactly; "
        "the widened _CONTROL_RE must not strip ASCII printable / "
        "German diacritic / hyphen / em-dash characters."
    )
