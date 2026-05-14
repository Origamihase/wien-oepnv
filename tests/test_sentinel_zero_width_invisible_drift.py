"""Sentinel PoC: the canonical Trojan-Source / invisible-character regex
union and its eight sibling validators do NOT cover the **zero-width
invisible Format characters** ``U+180E`` (MONGOLIAN VOWEL SEPARATOR)
and ``U+2060``-``U+2064`` (WORD JOINER, FUNCTION APPLICATION, INVISIBLE
TIMES, INVISIBLE SEPARATOR, INVISIBLE PLUS).

Every code point listed above is in Unicode General Category ``Cf``
(Format) — the same category as ``U+200B`` (ZWSP), ``U+200C`` (ZWNJ),
``U+200D`` (ZWJ), and the BiDi formatting controls already in the
canonical floor. Each is documented invisible (zero advance width in
every conforming renderer — terminals, browsers, GitHub web UI, IDE
preview, RSS readers, PDF viewers); none has a legitimate consumer in
this codebase's data path.

The current canonical floor (``src/utils/logging.py``
``_INVISIBLE_DANGEROUS_RE`` plus its eight sibling validators) covers
``\\u061c`` (ALM), ``\\u200b``-``\\u200f`` (ZWSP/ZWNJ/ZWJ + LRM/RLM),
``\\u2028``-``\\u202e`` (LSEP/PSEP + LRE/RLE/PDF/LRO/RLO),
``\\u2066``-``\\u2069`` (LRI/RLI/FSI/PDI), ``\\ufeff`` (BOM/ZWNBSP),
``\\ufe00``-``\\ufe0f`` (Variation Selectors 1-16), ``\\U000e0000``-
``\\U000e007f`` (Tag block), and ``\\U000e0100``-``\\U000e01ef``
(Variation Selectors 17-256). It does NOT cover the gap between
``\\u202e`` and ``\\u2066`` — specifically the ``\\u2060``-``\\u2064``
band — nor the legacy ``\\u180e``.

Threat model
============

Zero-width invisible Format characters are a documented attack
primitive in seven orthogonal sinks the codebase already hardens
against the Tag block / Variation Selector family:

1. **Cache JSON sidecars** — A planted upstream payload (provider
   compromise / OSM Overpass cache poisoning / VOR ReST hijack /
   compromised CI runner) carrying
   ``"name": "Hauptbahnhof\\u2060evil"`` (visually rendered
   ``Hauptbahnhofevil`` with zero space — the WJ glues the two tokens
   without producing a visible separator) survives
   :func:`src.utils.serialize.scrub_trojan_source_primitives` because
   the canonical attack-byte union ``_TROJAN_SOURCE_PRIMITIVES_RE``
   does NOT match ``\\u2060``. The poisoned name then reaches
   ``cache/<provider>/events.json``, ``data/stations.json``, and the
   weekly committed git diff — the WJ suffix is invisible in
   ``git log -p`` / ``git show`` / the GitHub web UI / ``cat`` /
   ``less`` / IDE preview.
2. **Operator-facing log lines** — A hostile upstream exception text
   carrying ``\\u2063`` (INVISIBLE SEPARATOR) flows through
   :func:`src.utils.logging.sanitize_log_message`. Pre-fix the
   always-strip floor ``_INVISIBLE_DANGEROUS_RE`` does NOT match
   the byte — it lands verbatim in ``log/diagnostics.log``,
   ``docs/feed_health.json`` (a public artefact published to GitHub
   Pages), and the GitHub Issue body auto-submitted on every fail.
3. **Public RSS feed** — Provider titles / descriptions / time-lines
   are routed through ``src/build_feed.py:_sanitize_text`` ->
   ``_CONTROL_RE``. WJ / Invisible Times / Invisible Separator bytes
   survive, land inside ``<title>`` / ``<description>`` of
   ``docs/feed.xml``, and reach every subscribed RSS reader.
4. **Stations directory** — ``data/stations.json`` entries flow
   through ``stations_validation._UNSAFE_CHARS_RE``. WJ bytes
   smuggled into ``name`` / ``aliases`` / ``bst_code`` / ``vor_id``
   slip past the validator and reach the published feed item that
   keys off the station name. Aliases like ``"Wien Hbf\\u2060"`` and
   ``"Wien Hbf"`` aggregate as DISTINCT entries downstream because
   the dedup key is byte-equality.
5. **Outbound HTTP URL boundary** — ``src/utils/http.py``
   ``_UNSAFE_URL_CHARS`` validates URLs before redirect / fetch /
   feed-link emission. Pre-fix a planted feed-item ``link`` with
   ``\\u2060`` payloads
   (``https://safe.example.com/path\\u2060segment`` — visually
   identical to ``https://safe.example.com/pathsegment`` but
   different bytes for cache-key / equality / GUID-collision shapes)
   passes the validator unmodified.
6. **Spreadsheet / CSV stats ledgers** — Zero-width payloads in
   provider names / location names planted via a poisoned cache
   survive ``_CSV_CONTROL_CHARS_RE`` and land in
   ``data/stats/<kind>_YYYY.csv``. Operators opening the CSV in
   Excel / LibreOffice Calc / Google Sheets see clean-looking text;
   downstream pivot-table aggregation keys on the **invisible**
   variants as distinct cells from the visible cousin, silently
   fracturing the operator's analytics view.
7. **Markdown sinks** — ``data/feed_health.md``, ``docs/statistik.md``,
   GitHub Issue bodies render Markdown via
   ``src/utils/text.py:_MARKDOWN_NORMALISE_UNSAFE_RE``. Zero-width
   bytes survive and reach the rendered Markdown verbatim.

Steganographic / prompt-injection surface
=========================================

The same family is also a documented prompt-injection smuggling
primitive. ``\\u2060``-``\\u2064`` are the canonical "invisible
Unicode steganography" alphabet in published research (e.g. Kasperski
2024 "ZWFP", "Sneaky Text" technique published 2023): combinations
of WJ + INVISIBLE TIMES + INVISIBLE SEPARATOR + INVISIBLE PLUS encode
arbitrary bytes that survive copy-paste from a log / RSS feed / GitHub
Issue body into an LLM context window — invisible to the human, fully
visible to the model. ``\\u180e`` adds a sixth bit of payload and
defeats every "strip ZWSP family" filter that relies on the
``\\u200x`` band.

Severity
========
**HIGH** — Trojan-Source-class display confusion AND prompt-injection
primitive. Public artefact (``docs/feed.xml`` /
``docs/feed_health.json`` / ``docs/feed-health.md`` / ``docs/sitemap.xml``)
served from ``https://origamihase.github.io/wien-oepnv/`` is the
attacker's published landing surface; every operator triaging off the
artefact, every RSS subscriber, every LLM training / RAG ingestion
pipeline that scrapes the feed inherits the smuggled payload.

Fix shape
=========
Mirror the canonical 2026-05-11 "Tag-Character / Variation-Selector
Drift" fix shape: extend the always-strip floor
``_INVISIBLE_DANGEROUS_RE`` with the new code-point bands and widen
each of the eight sibling validators in lockstep. The widening is
**additive only against the invisible-character family** — every
pre-fix-covered code point still matches post-fix; legitimate German
(ä/ö/ü/Ä/Ö/Ü/ß), CJK, and emoji content is untouched. The inventory
test ``test_sibling_regex_covers_canonical_invisible_dangerous_set``
in ``tests/test_sentinel_tag_chars_variation_selectors_invisible_drift.py``
provides the pinned sibling-superset invariant; the per-band tests
below assert the new code points are matched by every sibling.
"""

from __future__ import annotations

import json
import re
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


# Spot-check code points across each missing band.
_MISSING_ZERO_WIDTH_FORMATS: tuple[tuple[str, str], ...] = (
    ("᠎", "MONGOLIAN VOWEL SEPARATOR (U+180E)"),
    ("⁠", "WORD JOINER (U+2060)"),
    ("⁡", "FUNCTION APPLICATION (U+2061)"),
    ("⁢", "INVISIBLE TIMES (U+2062)"),
    ("⁣", "INVISIBLE SEPARATOR (U+2063)"),
    ("⁤", "INVISIBLE PLUS (U+2064)"),
)


# Pre-fix code points covered by the canonical regex — used by the
# additive-regression tests below to assert every pre-fix-covered code
# point still matches post-fix.
_PRE_FIX_COVERED_POINTS: tuple[str, ...] = (
    "\x00", "\x07", "\x1f", "\x7f",  # C0 / DEL
    "\x80", "\x9b", "\x9f",  # C1 (incl. 8-bit CSI)
    "؜",  # ALM
    "​", "‍", "‎", "‏",  # ZWSP / ZWJ / LRM / RLM
    "‪", "‮",  # LRE / RLO
    " ", " ",  # LSEP / PSEP
    "⁦", "⁩",  # LRI / PDI
    "﻿",  # BOM
    "︀", "️",  # VS-1 / VS-16
    "\U000e0020", "\U000e007f",  # TAG SPACE / CANCEL TAG
    "\U000e0100", "\U000e01ef",  # VS-17 / VS-256
)


# ---------------------------------------------------------------------------
# Unicode-category sanity — every PoC code point is a Format character
# (general category ``Cf``), the same class as ZWSP/ZWNJ/ZWJ. Pinning
# the category means a future Unicode-spec reclassification is caught
# at test-collection time rather than incident response.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code_point,label",
    _MISSING_ZERO_WIDTH_FORMATS,
    ids=[label for _, label in _MISSING_ZERO_WIDTH_FORMATS],
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
    _MISSING_ZERO_WIDTH_FORMATS,
    ids=[label for _, label in _MISSING_ZERO_WIDTH_FORMATS],
)
def test_invisible_dangerous_re_matches_missing_code_point(
    code_point: str, label: str
) -> None:
    """``_INVISIBLE_DANGEROUS_RE`` is the always-strip floor that every
    ``strip_control_chars=False`` sibling sink inherits. Pre-fix the
    regex jumps from ``\\u202e`` (RLO) to ``\\u2066`` (LRI), leaving
    the ``\\u2060``-``\\u2064`` band uncovered. ``\\u180e`` is in the
    same gap below the BiDi-Mark band.

    Post-fix the regex matches every code point in this PoC table.
    """
    assert canonical_logging._INVISIBLE_DANGEROUS_RE.search(code_point) is not None, (
        f"_INVISIBLE_DANGEROUS_RE must match {label}"
    )


@pytest.mark.parametrize(
    "code_point,label",
    _MISSING_ZERO_WIDTH_FORMATS,
    ids=[label for _, label in _MISSING_ZERO_WIDTH_FORMATS],
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
    _MISSING_ZERO_WIDTH_FORMATS,
    ids=[label for _, label in _MISSING_ZERO_WIDTH_FORMATS],
)
def test_trojan_source_primitives_re_matches_missing_code_point(
    code_point: str, label: str
) -> None:
    """``_TROJAN_SOURCE_PRIMITIVES_RE`` is the JSON-sidecar scrubber
    that every committed cache / state / stations writer runs on its
    payload before serialising. Pre-fix a planted zero-width
    payload reaches ``cache/<provider>/events.json`` /
    ``data/stations.json`` / ``data/places_quota.json`` verbatim.
    """
    assert _TROJAN_SOURCE_PRIMITIVES_RE.search(code_point) is not None, (
        f"_TROJAN_SOURCE_PRIMITIVES_RE must match {label}"
    )


@pytest.mark.parametrize(
    "code_point,label",
    _MISSING_ZERO_WIDTH_FORMATS,
    ids=[label for _, label in _MISSING_ZERO_WIDTH_FORMATS],
)
def test_unsafe_url_chars_matches_missing_code_point(
    code_point: str, label: str
) -> None:
    """``_UNSAFE_URL_CHARS`` is the URL boundary validator that gates
    every URL flowing into the published RSS feed, the sitemap, and
    outbound HTTP calls. Pre-fix a planted feed-item ``link`` with
    zero-width WJ / Invisible Times payloads passes the validator
    unmodified — the bytes land inside ``<link>`` of ``docs/feed.xml``
    and every subscriber's feed reader.
    """
    assert canonical_http._UNSAFE_URL_CHARS.search(code_point) is not None, (
        f"_UNSAFE_URL_CHARS must match {label}"
    )


@pytest.mark.parametrize(
    "code_point,label",
    _MISSING_ZERO_WIDTH_FORMATS,
    ids=[label for _, label in _MISSING_ZERO_WIDTH_FORMATS],
)
def test_csv_control_chars_re_matches_missing_code_point(
    code_point: str, label: str
) -> None:
    """``_CSV_CONTROL_CHARS_RE`` gates every text field written to
    ``data/stats/<kind>_YYYY.csv``. Pre-fix zero-width variants of a
    provider / location name silently fracture downstream pivot-table
    analytics — invisible-name cells aggregate separately from the
    visible cousin.
    """
    assert _CSV_CONTROL_CHARS_RE.search(code_point) is not None, (
        f"_CSV_CONTROL_CHARS_RE must match {label}"
    )


@pytest.mark.parametrize(
    "code_point,label",
    _MISSING_ZERO_WIDTH_FORMATS,
    ids=[label for _, label in _MISSING_ZERO_WIDTH_FORMATS],
)
def test_markdown_normalise_unsafe_re_matches_missing_code_point(
    code_point: str, label: str
) -> None:
    """``_MARKDOWN_NORMALISE_UNSAFE_RE`` gates every Markdown sink:
    ``data/feed_health.md``, ``docs/statistik.md``, the GitHub Issue
    body submitted by ``submit_auto_issue``. Pre-fix zero-width bytes
    flow through the rendered Markdown verbatim.
    """
    assert _MARKDOWN_NORMALISE_UNSAFE_RE.search(code_point) is not None, (
        f"_MARKDOWN_NORMALISE_UNSAFE_RE must match {label}"
    )


@pytest.mark.parametrize(
    "code_point,label",
    _MISSING_ZERO_WIDTH_FORMATS,
    ids=[label for _, label in _MISSING_ZERO_WIDTH_FORMATS],
)
def test_unsafe_chars_re_matches_missing_code_point(
    code_point: str, label: str
) -> None:
    """``_UNSAFE_CHARS_RE`` gates ``data/stations.json`` entry
    validation. Pre-fix zero-width bytes smuggled into ``name`` /
    ``aliases`` / ``bst_code`` / ``vor_id`` slip past the validator
    and reach the published feed.
    """
    assert _UNSAFE_CHARS_RE.search(code_point) is not None, (
        f"_UNSAFE_CHARS_RE must match {label}"
    )


@pytest.mark.parametrize(
    "code_point,label",
    _MISSING_ZERO_WIDTH_FORMATS,
    ids=[label for _, label in _MISSING_ZERO_WIDTH_FORMATS],
)
def test_build_feed_control_re_matches_missing_code_point(
    code_point: str, label: str
) -> None:
    """``src/build_feed.py:_CONTROL_RE`` is the LAST sanitiser before
    every RSS-feed title / description / time-line lands inside
    ``docs/feed.xml`` (served from
    ``https://origamihase.github.io/wien-oepnv/feed.xml``). Pre-fix
    zero-width bytes survive into the public RSS XML.
    """
    assert _CONTROL_RE.search(code_point) is not None, (
        f"_CONTROL_RE must match {label}"
    )


# ---------------------------------------------------------------------------
# End-to-end PoC tests — exercising the full pipeline.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code_point,label",
    _MISSING_ZERO_WIDTH_FORMATS,
    ids=[label for _, label in _MISSING_ZERO_WIDTH_FORMATS],
)
def test_sanitize_log_message_strips_missing_code_point_default(
    code_point: str, label: str
) -> None:
    """End-to-end: every missing code point is stripped by the public
    ``sanitize_log_message`` on the ``strip_control_chars=True``
    (default) path. Pre-fix the zero-width bytes flow verbatim into
    log/diagnostics.log / docs/feed_health.json / the GitHub Issue body.
    """
    payload = f"VOR error: {code_point}injected via invisible-format primitive"
    sanitized = canonical_logging.sanitize_log_message(payload)
    assert code_point not in sanitized, (
        f"{label} ({hex(ord(code_point))}) survived sanitize_log_message "
        f"(default): {sanitized!r}"
    )


@pytest.mark.parametrize(
    "code_point,label",
    _MISSING_ZERO_WIDTH_FORMATS,
    ids=[label for _, label in _MISSING_ZERO_WIDTH_FORMATS],
)
def test_sanitize_log_message_strips_missing_code_point_strip_disabled(
    code_point: str, label: str
) -> None:
    """``sanitize_log_message(strip_control_chars=False)`` is the path
    used by the traceback formatters and the canonical
    ``clean_message`` reporter sink. The always-strip floor MUST
    cover the zero-width Format band.
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
    _MISSING_ZERO_WIDTH_FORMATS,
    ids=[label for _, label in _MISSING_ZERO_WIDTH_FORMATS],
)
def test_scrub_trojan_source_primitives_strips_missing_code_point(
    code_point: str, label: str
) -> None:
    """Every JSON-sidecar writer (cache / stations / quota / state /
    quarantine / heartbeat / status) runs its payload through this
    scrubber before ``json.dump``. Pre-fix a planted zero-width
    payload reaches the committed sidecar verbatim.
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
    _MISSING_ZERO_WIDTH_FORMATS,
    ids=[label for _, label in _MISSING_ZERO_WIDTH_FORMATS],
)
def test_build_feed_sanitize_text_strips_missing_code_point(
    code_point: str, label: str
) -> None:
    """``_sanitize_text`` is the canonical RSS-XML sanitiser applied to
    every feed item title / description / time-line + the channel-level
    ``FEED_TITLE`` / ``FEED_DESC``. Zero-width bytes survive pre-fix
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
    _MISSING_ZERO_WIDTH_FORMATS,
    ids=[label for _, label in _MISSING_ZERO_WIDTH_FORMATS],
)
def test_normalise_markdown_text_strips_missing_code_point(
    code_point: str, label: str
) -> None:
    """``normalise_markdown_text`` gates every Markdown sink. Pre-fix
    zero-width bytes reach ``data/feed_health.md`` and
    ``docs/statistik.md`` rendered verbatim — invisible smuggling
    primitive into operator-facing reports.
    """
    payload = f"Provider X: {code_point}injected location"
    normalised = normalise_markdown_text(payload)
    assert code_point not in normalised, (
        f"{label} ({hex(ord(code_point))}) survived "
        f"normalise_markdown_text: {normalised!r}"
    )


@pytest.mark.parametrize(
    "code_point,label",
    _MISSING_ZERO_WIDTH_FORMATS,
    ids=[label for _, label in _MISSING_ZERO_WIDTH_FORMATS],
)
def test_sanitize_csv_text_field_strips_missing_code_point(
    code_point: str, label: str
) -> None:
    """``_sanitize_csv_text_field`` gates every provider / location
    field written to ``data/stats/<kind>_YYYY.csv``. Pre-fix zero-width
    variants silently fracture pivot-table aggregation.
    """
    payload = f"VOR{code_point}invisible"
    sanitized = _sanitize_csv_text_field(payload)
    assert code_point not in sanitized, (
        f"{label} ({hex(ord(code_point))}) survived "
        f"_sanitize_csv_text_field: {sanitized!r}"
    )


def test_feed_health_json_does_not_carry_zero_width_format_primitives() -> None:
    """End-to-end PoC: a hostile upstream exception text carrying
    ``\\u2060`` (WJ) and ``\\u2063`` (INVISIBLE SEPARATOR) flows
    through ``RunReport.record_exception`` -> ``clean_message`` -> the
    public ``docs/feed_health.json`` artefact via
    ``build_feed_health_payload`` -> ``json.dumps``.

    Pre-fix the zero-width bytes survive into the JSON. Post-fix the
    always-strip floor in ``_INVISIBLE_DANGEROUS_RE`` removes them so
    the published JSON carries only readable text.
    """
    # Visible "Failed" with WJ + Invisible Separator inserted between tokens.
    payload_chars = "⁠⁡⁢⁣⁤᠎"
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

    for code_point, label in _MISSING_ZERO_WIDTH_FORMATS:
        assert code_point not in rendered, (
            f"{label} ({hex(ord(code_point))}) leaked into the published "
            f"feed_health.json — rendered payload: {rendered!r}"
        )


def test_validate_http_url_rejects_planted_word_joiner_url() -> None:
    """End-to-end PoC: a planted feed-item ``link`` with a WORD JOINER
    suffix passes the URL validator pre-fix (the byte is not in any
    structural-URL component, just a path segment). Post-fix
    ``validate_http_url`` rejects the URL because the widened
    ``_UNSAFE_URL_CHARS`` catches the WJ byte.
    """
    planted = "https://example.com/path/seg⁠/end"
    result = canonical_http.validate_http_url(planted, check_dns=False)
    assert result is None, (
        f"validate_http_url MUST reject {planted!r} (planted WJ "
        "primitive); pre-fix it returned the URL unchanged so the bytes "
        "land inside <link> of docs/feed.xml."
    )


# ---------------------------------------------------------------------------
# Inventory invariants — every sibling regex MUST stay a superset of
# the canonical ``_INVISIBLE_DANGEROUS_RE``. Pre-fix every assert FAILS
# because the sibling regexes were narrower than the canonical floor.
# ---------------------------------------------------------------------------


def _canonical_code_points() -> list[int]:
    canonical = canonical_logging._INVISIBLE_DANGEROUS_RE
    return [cp for cp in range(0x110000) if canonical.fullmatch(chr(cp))]


def test_canonical_invisible_dangerous_re_covers_full_zero_width_format_set() -> None:
    """The canonical ``_INVISIBLE_DANGEROUS_RE`` MUST cover every code
    point in the zero-width Format gap-bands closed by this round.
    """
    canonical = canonical_logging._INVISIBLE_DANGEROUS_RE
    missing: list[int] = []
    if not canonical.fullmatch("᠎"):
        missing.append(0x180E)
    for cp in range(0x2060, 0x2065):  # WJ, FA, IT, IS, IP
        if not canonical.fullmatch(chr(cp)):
            missing.append(cp)
    assert not missing, (
        "_INVISIBLE_DANGEROUS_RE is narrower than the zero-width "
        f"Format closing-checklist; missing {len(missing)} code "
        "point(s): " + ", ".join(f"U+{cp:04X}" for cp in missing)
    )


@pytest.mark.parametrize(
    "name,regex",
    [
        ("_CONTROL_CHARS_RE", canonical_logging._CONTROL_CHARS_RE),
        ("_TROJAN_SOURCE_PRIMITIVES_RE", _TROJAN_SOURCE_PRIMITIVES_RE),
        ("_UNSAFE_URL_CHARS", canonical_http._UNSAFE_URL_CHARS),
        ("_CSV_CONTROL_CHARS_RE", _CSV_CONTROL_CHARS_RE),
        ("_MARKDOWN_NORMALISE_UNSAFE_RE", _MARKDOWN_NORMALISE_UNSAFE_RE),
        ("_UNSAFE_CHARS_RE", _UNSAFE_CHARS_RE),
        ("_CONTROL_RE", _CONTROL_RE),
    ],
)
def test_sibling_regex_covers_canonical_invisible_dangerous_set(
    name: str, regex: re.Pattern[str]
) -> None:
    """Inventory invariant: every code point matched by the canonical
    ``_INVISIBLE_DANGEROUS_RE`` MUST also match each sibling validator.

    Pre-fix the canonical floor was narrow enough that the seven
    sibling regexes accidentally satisfied the subset relation. Post-fix
    widening the canonical floor to cover the zero-width Format band
    immediately fails this test for every sibling that wasn't widened
    in the same PR — which is the point of this round's
    closing-checklist pinning.
    """
    canonical_code_points = _canonical_code_points()
    assert canonical_code_points, "Canonical regex matches nothing — regression"

    missing: list[int] = []
    for cp in canonical_code_points:
        if not regex.fullmatch(chr(cp)):
            missing.append(cp)

    assert not missing, (
        f"{name} is narrower than _INVISIBLE_DANGEROUS_RE; missing "
        f"{len(missing)} code point(s): "
        + ", ".join(f"U+{cp:04X}" for cp in missing[:20])
        + (" ..." if len(missing) > 20 else "")
        + f"\nThe two regexes must stay in sync. {name} must mirror "
        "every code point in the canonical always-strip floor or a "
        "planted payload carrying the unlisted code point flows past "
        "the sibling validator and reaches the public artefact / log "
        "line / cache JSON / RSS XML / CSV stats ledger."
    )


# ---------------------------------------------------------------------------
# Additive-regression invariants — every pre-fix-covered code point
# MUST still match post-fix. The widening must be additive.
# ---------------------------------------------------------------------------


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


@pytest.mark.parametrize(
    "code_point",
    _PRE_FIX_COVERED_POINTS,
    ids=[hex(ord(cp)) for cp in _PRE_FIX_COVERED_POINTS],
)
def test_unsafe_url_chars_preserves_existing_coverage(code_point: str) -> None:
    """Regression: ``_UNSAFE_URL_CHARS`` MUST keep matching every
    pre-fix code point.
    """
    assert canonical_http._UNSAFE_URL_CHARS.search(code_point), (
        f"Pre-fix-covered code point {hex(ord(code_point))} regressed "
        "in _UNSAFE_URL_CHARS"
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
