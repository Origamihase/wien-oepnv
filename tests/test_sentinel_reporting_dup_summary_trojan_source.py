"""Sentinel PoC: Trojan-Source / BiDi-mark drift at the duplicate-
summary inline-code-span sink (:mod:`docs/feed-health.md`) AND the
duplicate-summary JSON sink (:mod:`docs/feed-health.json`).

Threat model
------------

:func:`src.build_feed._summarize_duplicates` constructs
:class:`src.feed.reporting.DuplicateSummary` objects from raw provider
items. The ``dedupe_key`` is derived from each item's ``guid`` /
``_identity`` / ``link`` field; ``titles`` is derived from each item's
``title`` field. Both fields originate from upstream provider
responses (ÖBB, VOR, Wiener Linien), so a compromised provider, MITM,
DNS hijack, or poisoned cache fallback can plant Trojan-Source / BiDi
/ variation-selector / Tag-character bytes inside them.

Pre-fix sinks:

  1. ``docs/feed-health.md`` — :func:`src.feed.reporting.render_feed_health_markdown`
     interpolates ``dup.dedupe_key`` and each ``dup.titles`` element
     through :func:`src.feed.reporting._sanitize_code_span` at lines
     624/627. ``_sanitize_code_span`` only replaces literal backticks
     with apostrophes — it does NOT strip the canonical Trojan-Source
     family the sibling helper :func:`src.utils.text.safe_markdown_codespan`
     covers (the canonical floor pinned by ``_INVISIBLE_DANGEROUS_RE``).
  2. ``docs/feed-health.json`` — :func:`src.feed.reporting.build_feed_health_payload`
     writes ``summary.dedupe_key`` and ``summary.titles`` VERBATIM at
     lines 679/681. With ``json.dump(..., ensure_ascii=False)`` the
     BiDi / Tag-character / variation-selector bytes survive as raw
     UTF-8 in the committed JSON file.

Both sinks are published artefacts: ``docs/feed-health.md`` /
``docs/feed-health.json`` are committed to ``main`` by the
``update-cycle.yml`` cron workflow and served via GitHub Pages.

Attack shape
------------

  * BiDi RLO (U+202E): inverts displayed text after the mark in the
    rendered HTML — both inside the ``<code>`` element AND, for the
    JSON sink, in any UI that renders the JSON value.
  * Tag block (U+E0000-U+E007F): the canonical "ChatGPT invisible-
    instruction smuggling" primitive. Every printable ASCII codepoint
    has a paired Tag character rendering as zero-width. Smuggles
    arbitrary text inside a code span / JSON value that is invisible
    to a human reviewer but readable by LLM-driven downstream
    consumers (auto-summarisers, RSS-to-prompt pipelines).
  * Variation Selectors (U+FE00-U+FE0F, U+E0100-U+E01EF): 4-bit-
    payload steganography primitive. Same threat shape as Tag block.
  * Zero-width spaces (U+200B-U+200D), BOM (U+FEFF), ALM (U+061C),
    LRM/RLM (U+200E/200F), BiDi formatting (U+202A-U+202E), BiDi
    isolates (U+2066-U+2069): visual deception + cache-key /
    GUID-collision primitive.
  * ASCII C0 controls (\\x00-\\x08, \\x0b, \\x0c, \\x0e-\\x1f) and
    C1 controls + DEL (\\x7f-\\x9f): terminal-escape / log-injection
    primitive on the operator-facing stdout sink (``build_feed.py``
    line 2352) and the JSON sink consumed by SIEMs / log shippers.

Fix
---

  1. ``render_feed_health_markdown``: route ``dup.dedupe_key`` and each
     ``dup.titles`` element through :func:`src.utils.text.safe_markdown_codespan`
     (the canonical inline-code-span helper that strips the canonical
     floor + collapses whitespace + replaces backticks + caps length).
     Remove the local ``_sanitize_code_span`` helper as dead code.
  2. ``build_feed_health_payload``: route ``summary.dedupe_key`` and
     each ``summary.titles`` element through the canonical
     ``_CONTROL_CHARS_RE`` strip so the JSON sink emits clean
     UTF-8 bytes.

Severity: MEDIUM. Visual deception + steganographic smuggling + cache-
key collision + LLM-prompt-injection smuggling. No JS execution
(``<`` / ``>`` are HTML-entity-escaped earlier in the pipeline).
"""

from __future__ import annotations

import json
import re

import pytest

from src.build_feed import _summarize_duplicates
from src.feed.reporting import (
    DuplicateSummary,
    FeedHealthMetrics,
    RunReport,
    build_feed_health_payload,
    render_feed_health_markdown,
)
from src.feed_types import FeedItem


# ---------------------------------------------------------------------------
# Canonical "Trojan-Source primitive" enumeration. Mirrors the inventory
# pinned by ``tests/test_sentinel_tag_chars_variation_selectors_invisible_drift.py``
# and the canonical floor in ``src/utils/logging.py:_INVISIBLE_DANGEROUS_RE``.
# Every entry MUST be stripped by every downstream sink.
# ---------------------------------------------------------------------------
TROJAN_SOURCE_PRIMITIVES: tuple[tuple[str, str], ...] = (
    # ASCII C0 / C1 controls + DEL (subset — sample one per band).
    ("\x00", "NUL"),
    ("\x07", "BEL"),
    ("\x1b", "ESC"),
    ("\x7f", "DEL"),
    ("\x9b", "CSI (8-bit C1)"),
    # ALM / LRM / RLM / zero-width family.
    ("؜", "ALM"),
    ("​", "ZWSP"),
    ("‌", "ZWNJ"),
    ("‍", "ZWJ"),
    ("‎", "LRM"),
    ("‏", "RLM"),
    # Line / paragraph separators.
    (" ", "LSEP"),
    (" ", "PSEP"),
    # BiDi formatting (CVE-2021-42574 family).
    ("‪", "LRE"),
    ("‫", "RLE"),
    ("‬", "PDF"),
    ("‭", "LRO"),
    ("‮", "RLO"),
    # BiDi isolates.
    ("⁦", "LRI"),
    ("⁧", "RLI"),
    ("⁨", "FSI"),
    ("⁩", "PDI"),
    # Variation Selectors (BMP).
    ("︀", "VS1"),
    ("️", "VS16"),
    # BOM.
    ("﻿", "BOM"),
    # Unicode Tag block (LLM-smuggling primitive).
    ("\U000e0000", "TAG-START"),
    ("\U000e0061", "TAG-SMALL-A"),
    ("\U000e007f", "TAG-END"),
    # Supplementary Variation Selectors.
    ("\U000e0100", "VS17"),
    ("\U000e01ef", "VS256"),
)


def _empty_metrics_with(duplicates: tuple[DuplicateSummary, ...]) -> FeedHealthMetrics:
    return FeedHealthMetrics(
        raw_items=sum(d.count for d in duplicates),
        filtered_items=sum(d.count for d in duplicates),
        deduped_items=len(duplicates),
        new_items=0,
        duplicate_count=sum(d.count - 1 for d in duplicates),
        duplicates=duplicates,
    )


def _make_item(*, guid: str, title: str, source: str) -> FeedItem:
    """Build a minimal :class:`FeedItem` with the required TypedDict keys
    populated. ``_summarize_duplicates`` only reads ``guid`` / ``title``
    / ``link`` / ``_identity`` / ``source`` so the remaining required
    keys are filled with empty strings to satisfy the type checker.
    """
    return FeedItem(title=title, link="", description="", guid=guid, source=source)


# ---------------------------------------------------------------------------
# Markdown sink — ``render_feed_health_markdown``
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("primitive,name", TROJAN_SOURCE_PRIMITIVES)
def test_markdown_dedupe_key_strips_canonical_floor(primitive: str, name: str) -> None:
    """A Trojan-Source primitive in ``dup.dedupe_key`` MUST NOT survive
    into ``docs/feed-health.md``.
    """
    poisoned_key = f"oebb|guid-prefix{primitive}evil-suffix"
    dup = DuplicateSummary(dedupe_key=poisoned_key, count=2, titles=("Title A", "Title B"))
    markdown = render_feed_health_markdown(
        RunReport(statuses=[]), _empty_metrics_with((dup,))
    )

    assert primitive not in markdown, (
        f"Trojan-Source primitive {name!r} ({primitive!r}) survived from "
        f"dup.dedupe_key into the rendered Markdown. Pre-fix sink: "
        f"render_feed_health_markdown line 627 routes dedupe_key through "
        f"_sanitize_code_span which only replaces backticks. Use "
        f"safe_markdown_codespan instead."
    )


@pytest.mark.parametrize("primitive,name", TROJAN_SOURCE_PRIMITIVES)
def test_markdown_dup_title_strips_canonical_floor(primitive: str, name: str) -> None:
    """A Trojan-Source primitive in ``dup.titles[*]`` MUST NOT survive
    into ``docs/feed-health.md``.
    """
    poisoned_title = f"Normal title{primitive}with embedded primitive"
    dup = DuplicateSummary(
        dedupe_key="benign-key",
        count=2,
        titles=(poisoned_title, "other-title"),
    )
    markdown = render_feed_health_markdown(
        RunReport(statuses=[]), _empty_metrics_with((dup,))
    )

    assert primitive not in markdown, (
        f"Trojan-Source primitive {name!r} ({primitive!r}) survived from "
        f"dup.titles into the rendered Markdown. Pre-fix sink: "
        f"render_feed_health_markdown line 624 routes title through "
        f"_sanitize_code_span which only replaces backticks. Use "
        f"safe_markdown_codespan instead."
    )


def test_markdown_dedupe_key_legitimate_backtick_still_replaced() -> None:
    """Regression: the backtick-replacement contract MUST hold even
    after the canonical-floor scrub (mirrors the pre-fix
    ``_sanitize_code_span`` semantics for legitimate inputs).
    """
    dup = DuplicateSummary(
        dedupe_key="key`with`backticks",
        count=2,
        titles=("title", "other"),
    )
    markdown = render_feed_health_markdown(
        RunReport(statuses=[]), _empty_metrics_with((dup,))
    )
    bullet_line = next(
        line for line in markdown.splitlines() if "Schlüssel" in line and "2×" in line
    )
    # The dedupe-key cell contributes exactly two backticks (opening +
    # closing inline code span). A literal backtick inside the value
    # would break the count to four.
    backticks_in_key_cell = bullet_line.split(" – ", 1)[0].count("`")
    assert backticks_in_key_cell == 2, (
        f"Inline code span around dedupe_key MUST contain exactly two "
        f"backticks (opening + closing). Pre-fix the embedded backtick "
        f"would make it four. Got line: {bullet_line!r}"
    )


def test_markdown_dedupe_key_combined_bidi_and_backtick() -> None:
    """A combined Trojan-Source + backtick payload MUST be fully
    neutralised (canonical floor strips BiDi marks, backtick replaced).
    """
    poisoned_key = "key`with‮backtick-and-RLO"
    dup = DuplicateSummary(
        dedupe_key=poisoned_key, count=2, titles=("title", "other")
    )
    markdown = render_feed_health_markdown(
        RunReport(statuses=[]), _empty_metrics_with((dup,))
    )
    assert "‮" not in markdown
    # Backtick replaced with apostrophe (or canonically-equivalent form).
    bullet_line = next(
        line for line in markdown.splitlines() if "2×" in line
    )
    backticks_in_key_cell = bullet_line.split(" – ", 1)[0].count("`")
    assert backticks_in_key_cell == 2, (
        f"Combined attack MUST keep exactly two backticks "
        f"around the dedupe_key value. Got line: {bullet_line!r}"
    )


# ---------------------------------------------------------------------------
# JSON sink — ``build_feed_health_payload`` -> ``docs/feed-health.json``
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("primitive,name", TROJAN_SOURCE_PRIMITIVES)
def test_json_payload_dedupe_key_strips_canonical_floor(primitive: str, name: str) -> None:
    """A Trojan-Source primitive in ``summary.dedupe_key`` MUST NOT
    survive into ``docs/feed-health.json``.
    """
    poisoned_key = f"oebb|guid{primitive}suffix"
    dup = DuplicateSummary(dedupe_key=poisoned_key, count=2, titles=("a", "b"))
    payload = build_feed_health_payload(
        RunReport(statuses=[]), _empty_metrics_with((dup,))
    )
    dump = json.dumps(payload, ensure_ascii=False)
    assert primitive not in dump, (
        f"Trojan-Source primitive {name!r} ({primitive!r}) survived from "
        f"summary.dedupe_key into the JSON payload. Pre-fix sink: "
        f"build_feed_health_payload line 679 writes dedupe_key verbatim. "
        f"Apply a canonical-floor scrub to the value first."
    )


@pytest.mark.parametrize("primitive,name", TROJAN_SOURCE_PRIMITIVES)
def test_json_payload_title_strips_canonical_floor(primitive: str, name: str) -> None:
    """A Trojan-Source primitive in ``summary.titles[*]`` MUST NOT
    survive into ``docs/feed-health.json``.
    """
    poisoned_title = f"title{primitive}suffix"
    dup = DuplicateSummary(
        dedupe_key="benign", count=2, titles=(poisoned_title, "other")
    )
    payload = build_feed_health_payload(
        RunReport(statuses=[]), _empty_metrics_with((dup,))
    )
    dump = json.dumps(payload, ensure_ascii=False)
    assert primitive not in dump, (
        f"Trojan-Source primitive {name!r} ({primitive!r}) survived from "
        f"summary.titles into the JSON payload. Pre-fix sink: "
        f"build_feed_health_payload line 681 writes each title verbatim. "
        f"Apply a canonical-floor scrub to each title first."
    )


# ---------------------------------------------------------------------------
# Boundary — ``_summarize_duplicates`` (defence in depth)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("primitive,name", TROJAN_SOURCE_PRIMITIVES)
def test_summarize_duplicates_strips_canonical_floor_in_dedupe_key(
    primitive: str, name: str
) -> None:
    """An attacker-controlled provider ``guid`` carrying a Trojan-Source
    primitive MUST be scrubbed before reaching ``DuplicateSummary``.

    Defence-in-depth: even if a future renderer is added that consumes
    ``summary.dedupe_key`` without applying its own scrub, the value
    inside the dataclass is already clean.
    """
    poisoned_guid = f"https://oebb.at/guid{primitive}suffix"
    items: list[FeedItem] = [
        _make_item(guid=poisoned_guid, title="title-a", source="ÖBB"),
        _make_item(guid=poisoned_guid, title="title-b", source="ÖBB"),
    ]
    summaries = _summarize_duplicates(items)
    assert len(summaries) == 1
    assert primitive not in summaries[0].dedupe_key, (
        f"Trojan-Source primitive {name!r} ({primitive!r}) survived from "
        f"provider guid into DuplicateSummary.dedupe_key. The boundary "
        f"scrub at _summarize_duplicates MUST strip every canonical-floor "
        f"primitive before the dataclass is constructed."
    )


@pytest.mark.parametrize("primitive,name", TROJAN_SOURCE_PRIMITIVES)
def test_summarize_duplicates_strips_canonical_floor_in_titles(
    primitive: str, name: str
) -> None:
    """An attacker-controlled provider ``title`` carrying a Trojan-Source
    primitive MUST be scrubbed before reaching ``DuplicateSummary``.
    """
    poisoned_title = f"normal-text{primitive}suffix"
    items: list[FeedItem] = [
        _make_item(guid="shared-guid", title=poisoned_title, source="WL"),
        _make_item(guid="shared-guid", title="other", source="WL"),
    ]
    summaries = _summarize_duplicates(items)
    assert len(summaries) == 1
    joined_titles = "|".join(summaries[0].titles)
    assert primitive not in joined_titles, (
        f"Trojan-Source primitive {name!r} ({primitive!r}) survived from "
        f"provider title into DuplicateSummary.titles. The boundary "
        f"scrub at _summarize_duplicates MUST strip every canonical-floor "
        f"primitive before the dataclass is constructed."
    )


# ---------------------------------------------------------------------------
# Inventory invariants — pin the canonical-floor contract
# ---------------------------------------------------------------------------

def test_sanitize_code_span_helper_removed_or_canonical() -> None:
    """The drift helper ``_sanitize_code_span`` MUST be removed (or
    upgraded to mirror the canonical floor). A future regression that
    re-introduces a narrow helper fails this invariant.
    """
    import src.feed.reporting as reporting_mod

    helper = getattr(reporting_mod, "_sanitize_code_span", None)
    if helper is None:
        return  # Removed — best outcome.

    # If the helper is kept, it MUST strip every canonical-floor primitive.
    for primitive, name in TROJAN_SOURCE_PRIMITIVES:
        sample = f"a{primitive}b"
        out = helper(sample)
        assert primitive not in out, (
            f"_sanitize_code_span retained the canonical-floor primitive "
            f"{name!r} ({primitive!r}). Either remove the helper and use "
            f"safe_markdown_codespan instead, or extend the helper to mirror "
            f"the canonical floor."
        )


def test_render_feed_health_markdown_uses_canonical_codespan_helper() -> None:
    """``render_feed_health_markdown`` MUST route dedupe_key / titles
    through a sanitiser that mirrors the canonical floor — not the
    narrow ``_sanitize_code_span``.
    """
    import inspect

    import src.feed.reporting as reporting_mod

    source = inspect.getsource(reporting_mod.render_feed_health_markdown)
    # The body of the duplicate-summary section MUST reference the
    # canonical helper, not the legacy narrow one.
    assert "safe_markdown_codespan" in source, (
        "render_feed_health_markdown MUST use safe_markdown_codespan "
        "(the canonical inline-code-span helper that strips the "
        "canonical floor)."
    )
    # The legacy narrow helper MUST NOT appear in executed code paths.
    # Filter out comment lines so historical references in security
    # commentary do not trip the invariant.
    executable_lines = [
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    ]
    executable_source = "\n".join(executable_lines)
    assert "_sanitize_code_span(" not in executable_source, (
        "render_feed_health_markdown MUST NOT CALL the legacy "
        "_sanitize_code_span helper — the canonical safe_markdown_codespan "
        "is the single-sourced helper for inline code spans."
    )


def test_build_feed_health_payload_scrubs_dup_summary_fields() -> None:
    """``build_feed_health_payload`` MUST scrub ``dedupe_key`` and
    ``titles`` so the JSON sink emits clean UTF-8.
    """
    # Construct a poisoned DuplicateSummary directly (bypassing the
    # boundary at _summarize_duplicates) to validate the renderer's
    # own defence layer.
    poisoned_key = "key‮rlo"
    poisoned_title = "title\U000e0061tag"
    dup = DuplicateSummary(
        dedupe_key=poisoned_key, count=2, titles=(poisoned_title,)
    )
    payload = build_feed_health_payload(
        RunReport(statuses=[]), _empty_metrics_with((dup,))
    )
    duplicates = payload["duplicates"]
    assert len(duplicates) == 1
    entry = duplicates[0]
    assert "‮" not in entry["dedupe_key"], (
        "build_feed_health_payload MUST scrub U+202E (RLO) from dedupe_key. "
        "Defence-in-depth: even if a DuplicateSummary is constructed without "
        "going through _summarize_duplicates, the JSON sink MUST emit a clean "
        "value."
    )
    assert all("\U000e0061" not in t for t in entry["titles"]), (
        "build_feed_health_payload MUST scrub U+E0061 (TAG SMALL LETTER A) "
        "from titles. Defence-in-depth: the JSON sink MUST emit clean values "
        "even if the DuplicateSummary carries poisoned data."
    )


def test_dup_summary_attack_full_pipeline() -> None:
    """End-to-end PoC: a poisoned provider response MUST NOT leak any
    Trojan-Source primitive into either of the two published artefacts
    (``docs/feed-health.md`` or ``docs/feed-health.json``).
    """
    poisoned_payloads = (
        "‮payload-1",   # BiDi RLO
        "​payload-2",   # ZWSP
        "️payload-3",   # Variation Selector 16
        "\U000e0061payload-4",   # Tag Latin Small A
        "\U000e0100payload-5",   # Variation Selector 17 (supplementary)
        "؜payload-6",   # ALM
        "﻿payload-7",   # BOM
    )
    base_guid = "https://provider.example/guid"
    items: list[FeedItem] = []
    for idx, suffix in enumerate(poisoned_payloads):
        guid = f"{base_guid}{suffix}{idx}"
        items.append(_make_item(guid=guid, title=f"title{suffix}", source="VOR"))
        items.append(_make_item(guid=guid, title=f"other{suffix}", source="VOR"))

    summaries = _summarize_duplicates(items)
    metrics = _empty_metrics_with(tuple(summaries))

    # ---- Sink 1: Markdown ----
    markdown = render_feed_health_markdown(RunReport(statuses=[]), metrics)
    for primitive in poisoned_payloads:
        # Compare the first codepoint (the primitive) — the suffix
        # `payload-N` survives and that's expected.
        codepoint = primitive[0]
        assert codepoint not in markdown, (
            f"Trojan-Source primitive {codepoint!r} survived end-to-end "
            f"into docs/feed-health.md."
        )

    # ---- Sink 2: JSON ----
    payload = build_feed_health_payload(RunReport(statuses=[]), metrics)
    dump = json.dumps(payload, ensure_ascii=False)
    for primitive in poisoned_payloads:
        codepoint = primitive[0]
        assert codepoint not in dump, (
            f"Trojan-Source primitive {codepoint!r} survived end-to-end "
            f"into docs/feed-health.json."
        )


def test_dup_summary_legitimate_text_preserved() -> None:
    """Regression: legitimate ASCII / German-umlaut / transit-emoji
    content MUST be preserved through the boundary scrub. The scrub
    is additive only against the canonical-floor invisible family.
    """
    legitimate_key = "oebb|S-Bahn Stammstrecke Wien (S1/S2/S7)"
    legitimate_title = "Übergangsfahrplan für Bahnhöfe — geöffnet 06:00-22:00"
    dup = DuplicateSummary(
        dedupe_key=legitimate_key,
        count=2,
        titles=(legitimate_title, "Andere Variante"),
    )
    metrics = _empty_metrics_with((dup,))

    markdown = render_feed_health_markdown(RunReport(statuses=[]), metrics)
    # German umlauts MUST survive.
    assert "Übergangsfahrplan" in markdown
    assert "Bahnhöfe" in markdown
    assert "geöffnet" in markdown

    payload = build_feed_health_payload(RunReport(statuses=[]), metrics)
    assert payload["duplicates"][0]["dedupe_key"] == legitimate_key
    # Titles preserved (modulo any boundary normalisation — assert the
    # German umlauts survived intact).
    rendered_titles = payload["duplicates"][0]["titles"]
    assert any("Übergangsfahrplan" in t for t in rendered_titles)


def test_summarize_duplicates_preserves_group_count_under_attack() -> None:
    """The boundary scrub MUST NOT break the grouping logic — two items
    with the same poisoned guid still group together (the grouping uses
    the raw key, not the scrubbed one).
    """
    poisoned = "shared‮guid"
    items: list[FeedItem] = [
        _make_item(guid=poisoned, title="a", source="X"),
        _make_item(guid=poisoned, title="b", source="X"),
        _make_item(guid=poisoned, title="c", source="X"),
    ]
    summaries = _summarize_duplicates(items)
    assert len(summaries) == 1
    assert summaries[0].count == 3
    # The OUTPUT key has the RLO stripped.
    assert "‮" not in summaries[0].dedupe_key


# ---------------------------------------------------------------------------
# Newline / whitespace collapse — confirm Markdown layout integrity
# ---------------------------------------------------------------------------

def test_markdown_dedupe_key_newline_does_not_break_bullet_list() -> None:
    """A newline / CR / TAB inside ``dedupe_key`` MUST be whitespace-
    collapsed so the rendered ``- ...`` bullet stays on a single line.
    """
    poisoned = "key\nwith\rnewlines\tand-tabs"
    dup = DuplicateSummary(dedupe_key=poisoned, count=2, titles=("a", "b"))
    markdown = render_feed_health_markdown(
        RunReport(statuses=[]), _empty_metrics_with((dup,))
    )
    # Locate the bullet line for the duplicate summary.
    duplicate_block = re.search(
        r"### Entfernte Duplikate im Detail\n\n(- .+?)\n", markdown, re.DOTALL
    )
    assert duplicate_block is not None
    bullet = duplicate_block.group(1)
    assert "\n" not in bullet
