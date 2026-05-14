"""Sentinel PoC: the canonical Trojan-Source / invisible-character regex
union and its eight sibling validators do NOT cover **44 Unicode Format
characters** (general category ``Cf``) across **13 disjoint code-point
bands** that Unicode classifies as zero-width / invisible-by-default
formatting primitives in the same family as ZWSP/ZWNJ/ZWJ already in
the canonical floor.

The canonical floor pinned by every prior round (``_INVISIBLE_DANGEROUS_RE``
in ``src/utils/logging.py``, plus the eight sibling validators) covers
~119 of the 163 ``Cf``-class code points in Unicode 16.0. The 44 missing
code points span the following 13 bands:

  *  ``U+00AD``                 — SOFT HYPHEN (the canonical "invisible-by-
                                  default" character used in countless
                                  homograph / steganography attacks; only
                                  rendered at line-break opportunities).
  *  ``U+0600``-``U+0605``      — ARABIC NUMBER SIGN, ARABIC SIGN SANAH,
                                  ARABIC FOOTNOTE MARKER, ARABIC SIGN SAFHA,
                                  ARABIC SIGN SAMVAT, ARABIC NUMBER MARK
                                  ABOVE (zero-width Arabic prefix marks).
  *  ``U+06DD``                 — ARABIC END OF AYAH.
  *  ``U+070F``                 — SYRIAC ABBREVIATION MARK.
  *  ``U+0890``-``U+0891``      — ARABIC POUND / PIASTRE MARK ABOVE.
  *  ``U+08E2``                 — ARABIC DISPUTED END OF AYAH.
  *  ``U+206A``-``U+206F``      — Deprecated BiDi formatting controls
                                  (INHIBIT/ACTIVATE SYMMETRIC SWAPPING,
                                  INHIBIT/ACTIVATE ARABIC FORM SHAPING,
                                  NATIONAL/NOMINAL DIGIT SHAPES).
  *  ``U+FFF9``-``U+FFFB``      — INTERLINEAR ANNOTATION ANCHOR /
                                  SEPARATOR / TERMINATOR.
  *  ``U+110BD``                — KAITHI NUMBER SIGN.
  *  ``U+110CD``                — KAITHI NUMBER SIGN ABOVE.
  *  ``U+13430``-``U+13438``    — EGYPTIAN HIEROGLYPH JOINERS / SEGMENT
                                  formatting controls.
  *  ``U+1BCA0``-``U+1BCA3``    — SHORTHAND FORMAT LETTER OVERLAP /
                                  CONTINUING OVERLAP / DOWN STEP / UP STEP.
  *  ``U+1D173``-``U+1D17A``    — MUSICAL SYMBOL BEGIN / END BEAM / TIE /
                                  SLUR / PHRASE.

The structural invariant that justifies the additive widening: every
code point above is in Unicode general category ``Cf`` (Format) — the
same category as ZWSP/ZWNJ/ZWJ already covered by the floor. None has
any legitimate consumer in this codebase's data path (no Arabic /
Syriac / Egyptian-hieroglyph / shorthand / musical-notation content
flows through any provider feed, station name, sitemap URL, RSS title
or operator log line).

Threat model
============

The 13 missing bands are documented attack primitives across the seven
sinks the codebase already hardens against the Tag block / Variation
Selector / WJ family. The most impactful sub-families:

1. **U+00AD SOFT HYPHEN** is the single most dangerous omission:
   * Renders zero-width *unconditionally* in browsers / RSS readers /
     terminals / IDE preview / GitHub web UI when not at a line-break
     opportunity, but is **stored as a real character** in every
     downstream byte-equality / hash / GUID dedup key.
   * Has been used in real-world attacks since at least 2018
     (e.g. CVE-2018-19165 in IDN homographs, CVE-2021-43616 in npm
     package-name spoofing, the 2023 "Sneaky Text" research).
   * A planted upstream payload ``"Hbf­evil"`` (SOFT HYPHEN between
     visible tokens) reaches the published RSS feed as
     ``<title>Hbf­evil</title>`` and renders identical to ``Hbfevil``
     in every operator-facing report — but byte-distinct for cache
     keys, GUID dedup, and the autosubmitted GitHub Issue body the
     sentinel protects against impersonation.

2. **U+0600-U+0605, U+06DD, U+070F, U+0890-U+0891, U+08E2** are
   zero-width Arabic / Syriac formatting marks. Per Unicode UAX #9
   they have zero advance width when not preceded by a digit, so a
   planted ``"VOR{U+0604}error"`` at the front of an upstream
   exception text renders identical to ``"VORerror"`` in every
   RSS reader / Markdown sink / log line. None has a legitimate
   consumer in this codebase.

3. **U+206A-U+206F** are *deprecated* BiDi formatting controls per
   Unicode (INHIBIT/ACTIVATE SYMMETRIC SWAPPING, INHIBIT/ACTIVATE
   ARABIC FORM SHAPING, NATIONAL/NOMINAL DIGIT SHAPES). They render
   zero-width in every modern terminal / browser / RSS reader. The
   existing canonical floor already strips ``U+2060``-``U+2069``
   (the WJ + Invisible-* + BiDi-isolate band); folding in
   ``U+206A``-``U+206F`` to ``U+2060``-``U+206F`` closes the
   adjacent deprecated-controls band.

4. **U+FFF9-U+FFFB** (INTERLINEAR ANNOTATION ANCHOR / SEPARATOR /
   TERMINATOR) are Unicode "ruby annotation" formatting controls.
   They render zero-width except in dedicated CJK ruby renderers
   (no consumer in the Vienna ÖPNV pipeline). A planted payload
   ``"Wien Hbf{U+FFF9}{U+FFFB}phishing"`` smuggles steganographic
   bytes through the dedup key while rendering identical in the
   feed UI.

5. **U+110BD, U+110CD, U+13430-U+13438, U+1BCA0-U+1BCA3,
   U+1D173-U+1D17A** are supplementary-plane Format characters from
   the Kaithi / Egyptian Hieroglyphs / Shorthand / Musical Symbols
   blocks. None has a legitimate consumer in this codebase's
   ÖPNV-Vienna data path. They render zero-width in every
   non-specialist renderer, are documented zero-width in Unicode,
   and serve as additional steganography alphabet bytes that defeat
   any "strip BMP-only" filter that does not enumerate the
   supplementary planes.

Public sinks impacted
=====================

  *  ``docs/feed.xml``            — RSS XML serialiser via
                                    ``src/build_feed.py:_CONTROL_RE``.
  *  ``docs/feed_health.json``    — public health report via the
                                    ``_CONTROL_CHARS_RE`` family in
                                    ``src/feed/reporting.py`` and
                                    ``src/utils/logging.py``.
  *  ``docs/feed-health.md``      — operator-facing Markdown report
                                    via
                                    ``src/utils/text.py:_MARKDOWN_NORMALISE_UNSAFE_RE``.
  *  ``docs/sitemap.xml``         — sitemap URLs via
                                    ``scripts/generate_sitemap.py:_UNSAFE_URL_CHARS``.
  *  ``data/stats/<kind>_YYYY.csv``
                                  — pivot-table-aggregated provider /
                                    location names via
                                    ``src/utils/stats.py:_CSV_CONTROL_CHARS_RE``.
  *  ``data/stations.json``       — canonical station directory via
                                    ``src/utils/stations_validation.py:_UNSAFE_CHARS_RE``.
  *  ``cache/<provider>/events.json``
                                  — provider event sidecars via
                                    ``src/utils/serialize.py:_TROJAN_SOURCE_PRIMITIVES_RE``.
  *  ``log/diagnostics.log``      — operator log lines via
                                    ``src/utils/logging.py:sanitize_log_message``.
  *  GitHub Issue body            — auto-submitted on every fail via
                                    ``submit_auto_issue``.
  *  Outbound HTTP URL boundary   — every ``validate_http_url`` call
                                    via ``src/utils/http.py:_UNSAFE_URL_CHARS``.

Severity
========
**HIGH** — visual deception (Trojan-Source-class display confusion via
SOFT HYPHEN especially) + steganographic data smuggling + LLM prompt-
injection smuggling (the 44 code points double the alphabet of
invisible characters available to a smuggling attack vs. the prior
WJ-only round) + cache-key / GUID-collision primitive.

Fix shape
=========
Mirror the 2026-05-14 "Zero-Width Format Drift" round shape: extend
the always-strip floor ``_INVISIBLE_DANGEROUS_RE`` with the 13 new
code-point bands and widen each of the eight sibling validators in
lockstep. The widening is **additive only against the invisible-
character family** — every pre-fix-covered code point still matches
post-fix; legitimate German (ä/ö/ü/Ä/Ö/Ü/ß), CJK, and emoji content
is untouched.

The inventory test
``test_sibling_regex_covers_canonical_invisible_dangerous_set`` in
``tests/test_sentinel_zero_width_invisible_drift.py`` provides the
pinned sibling-superset invariant; widening the canonical floor
without widening every sibling immediately fails that test.
"""

from __future__ import annotations

import json
import unicodedata
from typing import Any

import pytest

from src.build_feed import _CONTROL_RE, _sanitize_text
from src.feed.reporting import (
    FeedHealthMetrics,
    RunReport,
    build_feed_health_payload,
)
from src.utils import http as canonical_http
from src.utils import logging as canonical_logging
from src.utils.serialize import (
    _TROJAN_SOURCE_PRIMITIVES_RE,
    scrub_trojan_source_primitives,
)
from src.utils.stations_validation import _UNSAFE_CHARS_RE
from src.utils.stats import _CSV_CONTROL_CHARS_RE, _sanitize_csv_text_field
from src.utils.text import _MARKDOWN_NORMALISE_UNSAFE_RE, normalise_markdown_text


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
# Unicode-category sanity — every PoC code point is a Format character
# (general category ``Cf``), the same class as ZWSP/ZWNJ/ZWJ. Pinning
# the category means a future Unicode-spec reclassification is caught
# at test-collection time rather than in incident response.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code_point,label",
    _MISSING_CF_FORMATS,
    ids=[label for _, label in _MISSING_CF_FORMATS],
)
def test_missing_code_point_is_format_character(
    code_point: str, label: str
) -> None:
    """Every PoC code point MUST be in Unicode general category ``Cf``
    (Format). This is the structural invariant that justifies the
    additive widening: Format characters have zero advance width in
    every conforming renderer and no legitimate consumer in this
    codebase's data path.
    """
    category = unicodedata.category(code_point)
    assert category == "Cf", (
        f"{label} ({hex(ord(code_point))}) expected Cf, got {category}; "
        "the additive widening assumes Format-character semantics."
    )


# ---------------------------------------------------------------------------
# Per-code-point bypass tests — every assert FAILS pre-fix.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code_point,label",
    _MISSING_CF_FORMATS,
    ids=[label for _, label in _MISSING_CF_FORMATS],
)
def test_invisible_dangerous_re_matches_missing_code_point(
    code_point: str, label: str
) -> None:
    """``_INVISIBLE_DANGEROUS_RE`` is the always-strip floor that every
    ``strip_control_chars=False`` sibling sink inherits. Pre-fix the
    13 missing Cf bands flow verbatim through the floor.
    """
    assert canonical_logging._INVISIBLE_DANGEROUS_RE.search(code_point) is not None, (
        f"_INVISIBLE_DANGEROUS_RE must match {label}"
    )


@pytest.mark.parametrize(
    "code_point,label",
    _MISSING_CF_FORMATS,
    ids=[label for _, label in _MISSING_CF_FORMATS],
)
def test_control_chars_re_matches_missing_code_point(
    code_point: str, label: str
) -> None:
    """``_CONTROL_CHARS_RE`` is the ``strip_control_chars=True`` (default)
    path's strip set. It MUST stay a superset of the always-strip floor.
    """
    assert canonical_logging._CONTROL_CHARS_RE.search(code_point) is not None, (
        f"_CONTROL_CHARS_RE must match {label}"
    )


@pytest.mark.parametrize(
    "code_point,label",
    _MISSING_CF_FORMATS,
    ids=[label for _, label in _MISSING_CF_FORMATS],
)
def test_trojan_source_primitives_re_matches_missing_code_point(
    code_point: str, label: str
) -> None:
    """``_TROJAN_SOURCE_PRIMITIVES_RE`` is the JSON-sidecar scrubber
    that every committed cache / state / stations writer runs on its
    payload before serialising. Pre-fix a planted Cf payload reaches
    every committed sidecar verbatim — invisible in
    ``git log -p`` / ``git show`` / GitHub web UI / IDE preview.
    """
    assert _TROJAN_SOURCE_PRIMITIVES_RE.search(code_point) is not None, (
        f"_TROJAN_SOURCE_PRIMITIVES_RE must match {label}"
    )


@pytest.mark.parametrize(
    "code_point,label",
    _MISSING_CF_FORMATS,
    ids=[label for _, label in _MISSING_CF_FORMATS],
)
def test_unsafe_url_chars_matches_missing_code_point(
    code_point: str, label: str
) -> None:
    """``_UNSAFE_URL_CHARS`` is the URL boundary validator that gates
    every URL flowing into the published RSS feed, the sitemap, and
    outbound HTTP calls. Pre-fix a planted feed-item ``link`` carrying
    SOFT HYPHEN / Arabic prefix / deprecated BiDi-control / interlinear
    / supplementary-plane Format bytes passes the validator unmodified.
    """
    assert canonical_http._UNSAFE_URL_CHARS.search(code_point) is not None, (
        f"_UNSAFE_URL_CHARS must match {label}"
    )


@pytest.mark.parametrize(
    "code_point,label",
    _MISSING_CF_FORMATS,
    ids=[label for _, label in _MISSING_CF_FORMATS],
)
def test_csv_control_chars_re_matches_missing_code_point(
    code_point: str, label: str
) -> None:
    """``_CSV_CONTROL_CHARS_RE`` gates every text field written to
    ``data/stats/<kind>_YYYY.csv``. Pre-fix Cf variants of a
    provider / location name silently fracture downstream pivot-table
    analytics — invisible-name cells aggregate separately from the
    visible cousin.
    """
    assert _CSV_CONTROL_CHARS_RE.search(code_point) is not None, (
        f"_CSV_CONTROL_CHARS_RE must match {label}"
    )


@pytest.mark.parametrize(
    "code_point,label",
    _MISSING_CF_FORMATS,
    ids=[label for _, label in _MISSING_CF_FORMATS],
)
def test_markdown_normalise_unsafe_re_matches_missing_code_point(
    code_point: str, label: str
) -> None:
    """``_MARKDOWN_NORMALISE_UNSAFE_RE`` gates every Markdown sink:
    ``data/feed_health.md``, ``docs/statistik.md``, the GitHub Issue
    body submitted by ``submit_auto_issue``. Pre-fix Cf bytes flow
    through the rendered Markdown verbatim.
    """
    assert _MARKDOWN_NORMALISE_UNSAFE_RE.search(code_point) is not None, (
        f"_MARKDOWN_NORMALISE_UNSAFE_RE must match {label}"
    )


@pytest.mark.parametrize(
    "code_point,label",
    _MISSING_CF_FORMATS,
    ids=[label for _, label in _MISSING_CF_FORMATS],
)
def test_unsafe_chars_re_matches_missing_code_point(
    code_point: str, label: str
) -> None:
    """``_UNSAFE_CHARS_RE`` gates ``data/stations.json`` entry
    validation. Pre-fix Cf bytes smuggled into ``name`` / ``aliases``
    / ``bst_code`` / ``vor_id`` slip past the validator and reach
    the published feed.
    """
    assert _UNSAFE_CHARS_RE.search(code_point) is not None, (
        f"_UNSAFE_CHARS_RE must match {label}"
    )


@pytest.mark.parametrize(
    "code_point,label",
    _MISSING_CF_FORMATS,
    ids=[label for _, label in _MISSING_CF_FORMATS],
)
def test_build_feed_control_re_matches_missing_code_point(
    code_point: str, label: str
) -> None:
    """``src/build_feed.py:_CONTROL_RE`` is the LAST sanitiser before
    every RSS-feed title / description / time-line lands inside
    ``docs/feed.xml`` (served from
    ``https://origamihase.github.io/wien-oepnv/feed.xml``). Pre-fix
    Cf bytes survive into the public RSS XML.
    """
    assert _CONTROL_RE.search(code_point) is not None, (
        f"_CONTROL_RE must match {label}"
    )


# ---------------------------------------------------------------------------
# End-to-end PoC tests — exercising the full pipeline.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code_point,label",
    _MISSING_CF_FORMATS,
    ids=[label for _, label in _MISSING_CF_FORMATS],
)
def test_sanitize_log_message_strips_missing_code_point_default(
    code_point: str, label: str
) -> None:
    """End-to-end: every missing code point is stripped by the public
    ``sanitize_log_message`` on the ``strip_control_chars=True``
    (default) path. Pre-fix the Cf bytes flow verbatim into
    log/diagnostics.log / docs/feed_health.json / the GitHub Issue body.
    """
    payload = f"VOR error: {code_point}injected via Cf-class invisible primitive"
    sanitized = canonical_logging.sanitize_log_message(payload)
    assert code_point not in sanitized, (
        f"{label} ({hex(ord(code_point))}) survived sanitize_log_message "
        f"(default): {sanitized!r}"
    )


@pytest.mark.parametrize(
    "code_point,label",
    _MISSING_CF_FORMATS,
    ids=[label for _, label in _MISSING_CF_FORMATS],
)
def test_sanitize_log_message_strips_missing_code_point_strip_disabled(
    code_point: str, label: str
) -> None:
    """``sanitize_log_message(strip_control_chars=False)`` is the path
    used by the traceback formatters and the canonical
    ``clean_message`` reporter sink. The always-strip floor MUST
    cover every Cf-class band.
    """
    payload = f"prefix{code_point}suffix"
    sanitized = canonical_logging.sanitize_log_message(
        payload, strip_control_chars=False
    )
    assert code_point not in sanitized, (
        f"{label} ({hex(ord(code_point))}) leaked through "
        f"sanitize_log_message(strip_control_chars=False): {sanitized!r}"
    )


@pytest.mark.parametrize(
    "code_point,label",
    _MISSING_CF_FORMATS,
    ids=[label for _, label in _MISSING_CF_FORMATS],
)
def test_scrub_trojan_source_primitives_strips_missing_code_point(
    code_point: str, label: str
) -> None:
    """Every JSON-sidecar writer (cache / stations / quota / state /
    quarantine / heartbeat / status) runs its payload through this
    scrubber before ``json.dump``. Pre-fix a planted Cf payload
    reaches the committed sidecar verbatim.
    """
    payload = {"name": f"Hauptbahnhof{code_point}evil"}
    scrubbed = scrub_trojan_source_primitives(payload)
    assert isinstance(scrubbed, dict)
    assert code_point not in scrubbed["name"], (
        f"{label} ({hex(ord(code_point))}) survived "
        f"scrub_trojan_source_primitives: {scrubbed!r}"
    )


@pytest.mark.parametrize(
    "code_point,label",
    _MISSING_CF_FORMATS,
    ids=[label for _, label in _MISSING_CF_FORMATS],
)
def test_build_feed_sanitize_text_strips_missing_code_point(
    code_point: str, label: str
) -> None:
    """``_sanitize_text`` is the canonical RSS-XML sanitiser applied to
    every feed item title / description / time-line + the channel-level
    ``FEED_TITLE`` / ``FEED_DESC``. Cf bytes survive pre-fix
    into ``docs/feed.xml`` and every subscriber's RSS reader.
    """
    payload = f"U6: Verspätung{code_point}injected"
    sanitized = _sanitize_text(payload)
    assert code_point not in sanitized, (
        f"{label} ({hex(ord(code_point))}) survived _sanitize_text: "
        f"{sanitized!r}"
    )


@pytest.mark.parametrize(
    "code_point,label",
    _MISSING_CF_FORMATS,
    ids=[label for _, label in _MISSING_CF_FORMATS],
)
def test_normalise_markdown_text_strips_missing_code_point(
    code_point: str, label: str
) -> None:
    """``normalise_markdown_text`` gates every Markdown sink. Pre-fix
    Cf bytes reach ``data/feed_health.md`` and ``docs/statistik.md``
    rendered verbatim — invisible smuggling primitive into operator-
    facing reports.
    """
    payload = f"Provider X: {code_point}injected location"
    normalised = normalise_markdown_text(payload)
    assert code_point not in normalised, (
        f"{label} ({hex(ord(code_point))}) survived "
        f"normalise_markdown_text: {normalised!r}"
    )


@pytest.mark.parametrize(
    "code_point,label",
    _MISSING_CF_FORMATS,
    ids=[label for _, label in _MISSING_CF_FORMATS],
)
def test_sanitize_csv_text_field_strips_missing_code_point(
    code_point: str, label: str
) -> None:
    """``_sanitize_csv_text_field`` gates every provider / location
    field written to ``data/stats/<kind>_YYYY.csv``. Pre-fix Cf
    variants silently fracture pivot-table aggregation.
    """
    payload = f"VOR{code_point}invisible"
    sanitized = _sanitize_csv_text_field(payload)
    assert code_point not in sanitized, (
        f"{label} ({hex(ord(code_point))}) survived "
        f"_sanitize_csv_text_field: {sanitized!r}"
    )


def test_feed_health_json_does_not_carry_cf_format_primitives() -> None:
    """End-to-end PoC: a hostile upstream exception text carrying
    SOFT HYPHEN + Arabic prefix marks + deprecated BiDi controls +
    interlinear annotations + supplementary-plane Format characters
    flows through ``RunReport.record_exception`` -> ``clean_message`` ->
    the public ``docs/feed_health.json`` artefact via
    ``build_feed_health_payload`` -> ``json.dumps``.

    Pre-fix the Cf bytes survive into the JSON. Post-fix the
    always-strip floor in ``_INVISIBLE_DANGEROUS_RE`` removes them so
    the published JSON carries only readable text.
    """
    # Multi-band payload: SOFT HYPHEN + Arabic + deprecated BiDi +
    # interlinear + Kaithi + Egyptian + shorthand + musical.
    payload_chars = (
        "­؀۝܏⁪⁯￹￻"
        "\U000110bd\U00013430\U0001bca0\U0001d173"
    )
    report = RunReport(statuses=[])
    report.record_exception(RuntimeError(f"VOR failed{payload_chars}injected"))

    metrics = FeedHealthMetrics(
        raw_items=0,
        filtered_items=0,
        deduped_items=0,
        new_items=0,
        duplicate_count=0,
        duplicates=(),
    )
    payload = build_feed_health_payload(report, metrics)
    rendered = json.dumps(payload, ensure_ascii=False)

    for code_point, label in _MISSING_CF_FORMATS:
        assert code_point not in rendered, (
            f"{label} ({hex(ord(code_point))}) leaked into the published "
            f"feed_health.json — rendered payload: {rendered!r}"
        )


def test_validate_http_url_rejects_planted_soft_hyphen_url() -> None:
    """End-to-end PoC: a planted feed-item ``link`` with a SOFT HYPHEN
    suffix passes the URL validator pre-fix (the byte is not in any
    structural-URL component, just a path segment). Post-fix
    ``validate_http_url`` rejects the URL because the widened
    ``_UNSAFE_URL_CHARS`` catches the SOFT HYPHEN byte.

    SOFT HYPHEN is the most impactful of the 13 missing bands: it
    renders zero-width unconditionally in browsers / RSS readers /
    terminals when not at a line-break opportunity, but is stored as
    a real character in every downstream byte-equality / hash / GUID
    dedup key.
    """
    planted = "https://example.com/path/seg­/end"
    result = canonical_http.validate_http_url(planted, check_dns=False)
    assert result is None, (
        f"validate_http_url MUST reject {planted!r} (planted SOFT "
        "HYPHEN primitive); pre-fix it returned the URL unchanged so "
        "the bytes land inside <link> of docs/feed.xml."
    )


def test_validate_http_url_rejects_planted_deprecated_bidi_url() -> None:
    """End-to-end PoC: a planted feed-item ``link`` with a deprecated
    BiDi control character (U+206E NATIONAL DIGIT SHAPES) passes the
    URL validator pre-fix. Post-fix ``validate_http_url`` rejects it.
    """
    planted = "https://example.com/path/seg⁮/end"
    result = canonical_http.validate_http_url(planted, check_dns=False)
    assert result is None, (
        f"validate_http_url MUST reject {planted!r} (planted deprecated "
        "BiDi-control primitive)."
    )


# ---------------------------------------------------------------------------
# Inventory invariants — pin the canonical-floor coverage of all 13 bands.
# ---------------------------------------------------------------------------


def test_canonical_invisible_dangerous_re_covers_full_cf_format_set() -> None:
    """The canonical ``_INVISIBLE_DANGEROUS_RE`` MUST cover every code
    point in the 13 zero-width Cf-Format bands closed by this round.

    The sibling-superset invariant in
    ``tests/test_sentinel_zero_width_invisible_drift.py:test_sibling_regex_covers_canonical_invisible_dangerous_set``
    inherits the new coverage automatically (every code point matched
    by the canonical floor must also match every sibling validator).
    """
    canonical = canonical_logging._INVISIBLE_DANGEROUS_RE
    expected_ranges: tuple[tuple[int, int], ...] = (
        (0x00AD, 0x00AD),
        (0x0600, 0x0605),
        (0x06DD, 0x06DD),
        (0x070F, 0x070F),
        (0x0890, 0x0891),
        (0x08E2, 0x08E2),
        (0x206A, 0x206F),
        (0xFFF9, 0xFFFB),
        (0x110BD, 0x110BD),
        (0x110CD, 0x110CD),
        (0x13430, 0x13438),
        (0x1BCA0, 0x1BCA3),
        (0x1D173, 0x1D17A),
    )
    missing: list[int] = []
    for start, end in expected_ranges:
        for cp in range(start, end + 1):
            if not canonical.fullmatch(chr(cp)):
                missing.append(cp)
    assert not missing, (
        "_INVISIBLE_DANGEROUS_RE is narrower than the Cf-Format closing-"
        f"checklist; missing {len(missing)} code point(s): "
        + ", ".join(f"U+{cp:04X}" for cp in missing)
    )


def test_canonical_invisible_dangerous_re_covers_every_unicode_cf_character() -> None:
    """Pin the structural invariant: every Unicode code point in
    general category ``Cf`` (Format) MUST be matched by the canonical
    always-strip floor.

    This is the stronger version of the per-band test above: it
    enumerates every Cf code point in Unicode 16.0 and asserts the
    floor matches each one. A future Unicode-spec addition of a new
    Format-category code point fails this test on the first pytest
    run after the new ``unicodedata`` ships, surfacing the next drift
    family programmatically per the prevention rule pinned in
    ``.jules/sentinel.md`` (2026-05-14 "Zero-Width Format Drift").
    """
    canonical = canonical_logging._INVISIBLE_DANGEROUS_RE
    missing: list[int] = []
    for cp in range(0x110000):
        if unicodedata.category(chr(cp)) == "Cf":
            if not canonical.fullmatch(chr(cp)):
                missing.append(cp)
    assert not missing, (
        f"_INVISIBLE_DANGEROUS_RE is missing {len(missing)} Cf code "
        "point(s): "
        + ", ".join(
            f"U+{cp:04X} ({unicodedata.name(chr(cp), '<no name>')})"
            for cp in missing[:30]
        )
        + (" ..." if len(missing) > 30 else "")
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


@pytest.mark.parametrize(
    "code_point",
    _PRE_FIX_COVERED_POINTS,
    ids=[hex(ord(cp)) for cp in _PRE_FIX_COVERED_POINTS],
)
def test_canonical_invisible_dangerous_re_preserves_existing_coverage(
    code_point: str,
) -> None:
    """Regression: every code point ``_INVISIBLE_DANGEROUS_RE`` matched
    pre-fix MUST still match post-fix. The widening must be additive.
    """
    assert canonical_logging._INVISIBLE_DANGEROUS_RE.search(code_point), (
        f"Pre-fix-covered code point {hex(ord(code_point))} regressed"
    )


def test_legitimate_german_text_passes_all_sibling_regexes() -> None:
    """Regression: legitimate German station names with umlauts ä/ö/ü/
    Ä/Ö/Ü + sharp s ß + emoji 🚇 MUST NOT match any sibling regex.
    The widening is additive against the invisible-character family
    only; printable Unicode content stays untouched.
    """
    legit_chars = "äöüÄÖÜß🚇"
    for name, regex in (
        ("_INVISIBLE_DANGEROUS_RE", canonical_logging._INVISIBLE_DANGEROUS_RE),
        ("_CONTROL_CHARS_RE", canonical_logging._CONTROL_CHARS_RE),
        ("_TROJAN_SOURCE_PRIMITIVES_RE", _TROJAN_SOURCE_PRIMITIVES_RE),
        ("_UNSAFE_URL_CHARS", canonical_http._UNSAFE_URL_CHARS),
        ("_CSV_CONTROL_CHARS_RE", _CSV_CONTROL_CHARS_RE),
        ("_MARKDOWN_NORMALISE_UNSAFE_RE", _MARKDOWN_NORMALISE_UNSAFE_RE),
        ("_UNSAFE_CHARS_RE", _UNSAFE_CHARS_RE),
        ("_CONTROL_RE", _CONTROL_RE),
    ):
        ascii_letters = "WienHauptbahnhof"
        assert regex.search(ascii_letters) is None, (
            f"{name} unexpectedly matched legitimate ASCII content"
        )
        for char in legit_chars:
            assert regex.search(char) is None, (
                f"{name} unexpectedly matched legitimate "
                f"non-ASCII content: {hex(ord(char))}"
            )


def test_scrub_trojan_source_primitives_preserves_german_umlauts() -> None:
    """Regression: the scrubber must leave German umlauts and emoji
    untouched. The defence is additive against the canonical invisible-
    character family, not a blanket non-ASCII strip.
    """
    payload: dict[str, Any] = {
        "name": "Wien Hauptbahnhof",
        "aliases": ["Wien Hbf", "Hauptbahnhof Wien"],
        "city": "Wien",
        "umlauts": "äöüÄÖÜß",
        "emoji": "🚇 🚆",
    }
    scrubbed = scrub_trojan_source_primitives(payload)
    assert scrubbed == payload, (
        f"German content was modified by the scrubber: {scrubbed!r}"
    )


def test_soft_hyphen_smuggled_dedup_key_collapse_in_stations_directory() -> None:
    """End-to-end PoC: SOFT HYPHEN smuggled into a station ``aliases``
    field passes ``_UNSAFE_CHARS_RE`` pre-fix. The published feed item
    keys off the (unsanitised) name, so two visually-identical aliases
    aggregate as DISTINCT entries downstream.

    Post-fix ``_UNSAFE_CHARS_RE`` matches SOFT HYPHEN, so the
    ``stations.json`` security-issue scanner flags the entry and the
    ``submit_auto_issue`` loop alerts the operator.
    """
    visible = "Wien Hbf"
    stealth = "Wien­Hbf"
    assert _UNSAFE_CHARS_RE.search(visible) is None, (
        "Regression: legitimate ASCII alias unexpectedly matched _UNSAFE_CHARS_RE"
    )
    assert _UNSAFE_CHARS_RE.search(stealth) is not None, (
        f"_UNSAFE_CHARS_RE must flag {stealth!r} (planted SOFT HYPHEN); "
        "pre-fix the validator returned no match so the byte-distinct "
        "alias landed in data/stations.json and aggregated as a "
        "separate entry from 'Wien Hbf' downstream."
    )
