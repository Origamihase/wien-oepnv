"""Teil 1 der Tests aus tests/test_sentinel_cf_format_drift_round.py.

Beschreibung in __init__.py.
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

from ._support import (
    _MISSING_CF_FORMATS,
    _PRE_FIX_COVERED_POINTS,
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
    """``src/feed_build/base.py:_CONTROL_RE`` is the LAST sanitiser before
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
    ``tests/test_sentinel_zero_width_invisible_drift/:test_sibling_regex_covers_canonical_invisible_dangerous_set``
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
        (0x13430, 0x1343F),  # U+13439..U+1343F: Unicode 15.0 (2022)
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
    family programmatically per the prevention rule pinned by the
    2026-05-14 "Zero-Width Format Drift" audit.
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
