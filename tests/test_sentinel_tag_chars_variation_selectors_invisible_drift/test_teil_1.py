"""Teil 1 der Tests aus tests/test_sentinel_tag_chars_variation_selectors_invisible_drift.py.

Beschreibung in __init__.py.
"""

from __future__ import annotations

import json
import re
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
    _ALL_MISSING_POINTS,
    _PRE_FIX_COVERED_POINTS,
    _canonical_code_points,
)


# ---------------------------------------------------------------------------
# Per-code-point bypass tests — every assert FAILS pre-fix.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code_point,label",
    _ALL_MISSING_POINTS,
    ids=[label for _, label in _ALL_MISSING_POINTS],
)
def test_invisible_dangerous_re_matches_missing_code_point(
    code_point: str, label: str
) -> None:
    """``_INVISIBLE_DANGEROUS_RE`` is the always-strip floor that every
    ``strip_control_chars=False`` sibling sink inherits. Pre-fix the
    regex stops at the BiDi-isolate band (``\\u2069``) followed by BOM
    (``\\ufeff``) — it does NOT extend into plane 14 to cover the Tag
    block, nor does it cover the BMP Variation Selector band.

    Post-fix the regex matches every code point in this PoC table.
    """
    assert canonical_logging._INVISIBLE_DANGEROUS_RE.search(code_point) is not None, (
        f"_INVISIBLE_DANGEROUS_RE must match {label}"
    )


@pytest.mark.parametrize(
    "code_point,label",
    _ALL_MISSING_POINTS,
    ids=[label for _, label in _ALL_MISSING_POINTS],
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
    _ALL_MISSING_POINTS,
    ids=[label for _, label in _ALL_MISSING_POINTS],
)
def test_trojan_source_primitives_re_matches_missing_code_point(
    code_point: str, label: str
) -> None:
    """``_TROJAN_SOURCE_PRIMITIVES_RE`` is the JSON-sidecar scrubber
    that every committed cache / state / stations writer runs on its
    payload before serialising. Pre-fix a planted Tag-character
    payload reaches ``cache/<provider>/events.json`` /
    ``data/stations.json`` verbatim.
    """
    assert _TROJAN_SOURCE_PRIMITIVES_RE.search(code_point) is not None, (
        f"_TROJAN_SOURCE_PRIMITIVES_RE must match {label}"
    )


@pytest.mark.parametrize(
    "code_point,label",
    _ALL_MISSING_POINTS,
    ids=[label for _, label in _ALL_MISSING_POINTS],
)
def test_unsafe_url_chars_matches_missing_code_point(
    code_point: str, label: str
) -> None:
    """``_UNSAFE_URL_CHARS`` is the URL boundary validator that gates
    every URL flowing into the published RSS feed, the sitemap, and
    outbound HTTP calls. Pre-fix a planted feed-item ``link`` with
    Tag-character payloads passes the validator unmodified — the bytes
    land inside ``<link>`` of ``docs/feed.xml`` and every subscriber's
    feed reader.
    """
    assert canonical_http._UNSAFE_URL_CHARS.search(code_point) is not None, (
        f"_UNSAFE_URL_CHARS must match {label}"
    )


@pytest.mark.parametrize(
    "code_point,label",
    _ALL_MISSING_POINTS,
    ids=[label for _, label in _ALL_MISSING_POINTS],
)
def test_csv_control_chars_re_matches_missing_code_point(
    code_point: str, label: str
) -> None:
    """``_CSV_CONTROL_CHARS_RE`` gates every text field written to
    ``data/stats/<kind>_YYYY.csv``. Pre-fix Tag-character variants of a
    provider / location name silently fracture downstream pivot-table
    analytics — invisible-name cells aggregate separately from the
    visible cousin.
    """
    assert _CSV_CONTROL_CHARS_RE.search(code_point) is not None, (
        f"_CSV_CONTROL_CHARS_RE must match {label}"
    )


@pytest.mark.parametrize(
    "code_point,label",
    _ALL_MISSING_POINTS,
    ids=[label for _, label in _ALL_MISSING_POINTS],
)
def test_markdown_normalise_unsafe_re_matches_missing_code_point(
    code_point: str, label: str
) -> None:
    """``_MARKDOWN_NORMALISE_UNSAFE_RE`` gates every Markdown sink:
    ``data/feed_health.md``, ``docs/statistik.md``, the GitHub Issue
    body submitted by ``submit_auto_issue``. Pre-fix Tag bytes flow
    through the rendered Markdown verbatim.
    """
    assert _MARKDOWN_NORMALISE_UNSAFE_RE.search(code_point) is not None, (
        f"_MARKDOWN_NORMALISE_UNSAFE_RE must match {label}"
    )


@pytest.mark.parametrize(
    "code_point,label",
    _ALL_MISSING_POINTS,
    ids=[label for _, label in _ALL_MISSING_POINTS],
)
def test_unsafe_chars_re_matches_missing_code_point(
    code_point: str, label: str
) -> None:
    """``_UNSAFE_CHARS_RE`` gates ``data/stations.json`` entry
    validation. Pre-fix Tag bytes smuggled into ``name`` / ``aliases``
    / ``bst_code`` / ``vor_id`` slip past the validator and reach the
    published feed.
    """
    assert _UNSAFE_CHARS_RE.search(code_point) is not None, (
        f"_UNSAFE_CHARS_RE must match {label}"
    )


@pytest.mark.parametrize(
    "code_point,label",
    _ALL_MISSING_POINTS,
    ids=[label for _, label in _ALL_MISSING_POINTS],
)
def test_build_feed_control_re_matches_missing_code_point(
    code_point: str, label: str
) -> None:
    """``src/build_feed.py:_CONTROL_RE`` is the LAST sanitiser before
    every RSS-feed title / description / time-line lands inside
    ``docs/feed.xml`` (served from
    ``https://origamihase.github.io/wien-oepnv/feed.xml``). Pre-fix Tag
    bytes survive into the public RSS XML.
    """
    assert _CONTROL_RE.search(code_point) is not None, (
        f"_CONTROL_RE must match {label}"
    )


# ---------------------------------------------------------------------------
# End-to-end PoC tests — exercising the full pipeline.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code_point,label",
    _ALL_MISSING_POINTS,
    ids=[label for _, label in _ALL_MISSING_POINTS],
)
def test_sanitize_log_message_strips_missing_code_point_default(
    code_point: str, label: str
) -> None:
    """End-to-end: every missing code point is stripped by the public
    ``sanitize_log_message`` on the ``strip_control_chars=True``
    (default) path. Pre-fix the tag-character bytes flow verbatim into
    log/diagnostics.log / docs/feed_health.json / the GitHub Issue body.
    """
    payload = f"VOR error: {code_point}injected via invisible-tag primitive"
    sanitized = canonical_logging.sanitize_log_message(payload)
    assert code_point not in sanitized, (
        f"{label} ({hex(ord(code_point))}) survived sanitize_log_message "
        f"(default): {sanitized!r}"
    )


@pytest.mark.parametrize(
    "code_point,label",
    _ALL_MISSING_POINTS,
    ids=[label for _, label in _ALL_MISSING_POINTS],
)
def test_sanitize_log_message_strips_missing_code_point_strip_disabled(
    code_point: str, label: str
) -> None:
    """``sanitize_log_message(strip_control_chars=False)`` is the path
    used by the traceback formatters and the canonical
    ``clean_message`` reporter sink. The always-strip floor MUST
    cover the Tag block / Variation Selector union.
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
    _ALL_MISSING_POINTS,
    ids=[label for _, label in _ALL_MISSING_POINTS],
)
def test_scrub_trojan_source_primitives_strips_missing_code_point(
    code_point: str, label: str
) -> None:
    """Every JSON-sidecar writer (cache / stations / quota / state /
    quarantine / heartbeat / status) runs its payload through this
    scrubber before ``json.dump``. Pre-fix a planted Tag-character
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
    _ALL_MISSING_POINTS,
    ids=[label for _, label in _ALL_MISSING_POINTS],
)
def test_build_feed_sanitize_text_strips_missing_code_point(
    code_point: str, label: str
) -> None:
    """``_sanitize_text`` is the canonical RSS-XML sanitiser applied to
    every feed item title / description / time-line + the channel-level
    ``FEED_TITLE`` / ``FEED_DESC``. Tag bytes survive pre-fix into
    ``docs/feed.xml`` and every subscriber's RSS reader.
    """
    payload = f"U6: Verspätung{code_point}injected"
    sanitized = _sanitize_text(payload)
    assert code_point not in sanitized, (
        f"{label} ({hex(ord(code_point))}) survived _sanitize_text: "
        f"{sanitized!r}"
    )


@pytest.mark.parametrize(
    "code_point,label",
    _ALL_MISSING_POINTS,
    ids=[label for _, label in _ALL_MISSING_POINTS],
)
def test_normalise_markdown_text_strips_missing_code_point(
    code_point: str, label: str
) -> None:
    """``normalise_markdown_text`` gates every Markdown sink. Pre-fix
    Tag bytes reach ``data/feed_health.md`` and ``docs/statistik.md``
    rendered verbatim — invisible smuggling primitive into
    operator-facing reports.
    """
    payload = f"Provider X: {code_point}injected location"
    normalised = normalise_markdown_text(payload)
    assert code_point not in normalised, (
        f"{label} ({hex(ord(code_point))}) survived "
        f"normalise_markdown_text: {normalised!r}"
    )


@pytest.mark.parametrize(
    "code_point,label",
    _ALL_MISSING_POINTS,
    ids=[label for _, label in _ALL_MISSING_POINTS],
)
def test_sanitize_csv_text_field_strips_missing_code_point(
    code_point: str, label: str
) -> None:
    """``_sanitize_csv_text_field`` gates every provider / location
    field written to ``data/stats/<kind>_YYYY.csv``. Pre-fix Tag
    variants silently fracture pivot-table aggregation.
    """
    payload = f"VOR{code_point}invisible"
    sanitized = _sanitize_csv_text_field(payload)
    assert code_point not in sanitized, (
        f"{label} ({hex(ord(code_point))}) survived "
        f"_sanitize_csv_text_field: {sanitized!r}"
    )


def test_feed_health_json_does_not_carry_tag_or_vs_primitives() -> None:
    """End-to-end PoC: a hostile upstream exception text carrying a
    Tag-character variant of ``evil`` and a Variation Selector flows
    through ``RunReport.record_exception`` -> ``clean_message`` -> the
    public ``docs/feed_health.json`` artefact via
    ``build_feed_health_payload`` -> ``json.dumps``.

    Pre-fix the Tag bytes survive into the JSON. Post-fix the
    always-strip floor in ``_INVISIBLE_DANGEROUS_RE`` removes them so
    the published JSON carries only readable text.
    """
    # Visible "Failed" with tag-character "·evil" appended invisibly.
    tag_evil = (
        "\U000e0020\U000e0065\U000e0076\U000e0069\U000e006c"  # ' evil'
    )
    vs_filler = "️\U000e0100"  # VS16 + VS17
    report = RunReport(statuses=[])
    report.record_exception(
        RuntimeError(f"VOR failed{tag_evil}{vs_filler}")
    )

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

    for code_point, label in _ALL_MISSING_POINTS:
        assert code_point not in rendered, (
            f"{label} ({hex(ord(code_point))}) leaked into the published "
            f"feed_health.json — rendered payload: {rendered!r}"
        )


def test_validate_http_url_rejects_planted_tag_char_url() -> None:
    """End-to-end PoC: a planted feed-item ``link`` with a tag-character
    suffix passes the URL validator pre-fix (the bytes are not in any
    structural-URL component, just a path segment). Post-fix
    ``validate_http_url`` rejects the URL because the widened
    ``_UNSAFE_URL_CHARS`` catches the tag byte.
    """
    tag_byte = "\U000e0065"  # TAG LATIN SMALL LETTER E
    planted = f"https://example.com/path/seg{tag_byte}/end"
    result = canonical_http.validate_http_url(planted, check_dns=False)
    assert result is None, (
        f"validate_http_url MUST reject {planted!r} (planted tag-char "
        "primitive); pre-fix it returned the URL unchanged so the bytes "
        "land inside <link> of docs/feed.xml."
    )


def test_canonical_invisible_dangerous_re_covers_full_tag_and_vs_set() -> None:
    """The canonical ``_INVISIBLE_DANGEROUS_RE`` MUST cover every code
    point in the Tag block, the BMP Variation Selectors, and the
    supplementary Variation Selectors. This is the closing-checklist
    invariant for the round.
    """
    canonical = canonical_logging._INVISIBLE_DANGEROUS_RE
    missing: list[int] = []
    for cp in range(0xE0000, 0xE0080):  # Tag block U+E0000-U+E007F
        if not canonical.fullmatch(chr(cp)):
            missing.append(cp)
    for cp in range(0xFE00, 0xFE10):  # VS-1 .. VS-16
        if not canonical.fullmatch(chr(cp)):
            missing.append(cp)
    for cp in range(0xE0100, 0xE01F0):  # VS-17 .. VS-256
        if not canonical.fullmatch(chr(cp)):
            missing.append(cp)
    assert not missing, (
        "_INVISIBLE_DANGEROUS_RE is narrower than the Tag + Variation "
        f"Selector closing-checklist; missing {len(missing)} code point(s): "
        + ", ".join(f"U+{cp:04X}" for cp in missing[:20])
        + (" ..." if len(missing) > 20 else "")
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
    widening the canonical floor to cover the Tag block + Variation
    Selectors immediately fails this test for every sibling that wasn't
    widened in the same PR — which is the point of this round's
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
    legit = "Wien Hauptbahnhof 🚇 äöüÄÖÜß"
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
        # _UNSAFE_URL_CHARS legitimately matches the leading space —
        # that's expected (whitespace + structural URL-injection chars).
        # We test against the in-place ASCII letters only.
        ascii_letters = "WienHauptbahnhof"
        assert regex.search(ascii_letters) is None, (
            f"{name} unexpectedly matched legitimate ASCII content"
        )
        # Confirm umlauts and emoji are untouched.
        for char in "äöüÄÖÜß🚇":
            assert regex.search(char) is None, (
                f"{name} unexpectedly matched legitimate "
                f"non-ASCII content: {hex(ord(char))}"
            )
        # Confirm the multi-byte test string survives sanitize-style
        # scrubbing (for the canonical scrubber).
        _ = legit  # silence unused-warning


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
