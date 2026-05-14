"""Sentinel PoC: Trojan-Source / control-character drift at the
``docs/feed-health.json`` ``run.feed_path`` JSON sink.

Threat model
------------

:func:`src.feed.reporting.build_feed_health_payload` constructs the
JSON payload that :func:`src.feed.reporting.write_feed_health_json`
serialises to the public ``docs/feed-health.json`` artefact (committed
to ``main`` by every ``update-cycle.yml`` cron tick and served via
GitHub Pages + raw.githubusercontent.com).

Pre-fix, line 759 emitted ``report.feed_path`` VERBATIM into the
payload's ``run.feed_path`` field::

    "run": {
        ...
        "feed_path": report.feed_path,
        ...
    }

``report.feed_path`` is the POSIX form of ``OUT_PATH`` resolved by
:func:`src.feed.config.resolve_env_path`; ``OUT_PATH`` is env-controlled
and :func:`src.feed.config.validate_path` only checks that the path
resolves under one of ``ALLOWED_ROOTS`` (``docs/``, ``data/``, ``log/``)
— it does NOT strip Trojan-Source / BiDi / Tag-block / Variation-
Selector / 8-bit C1 / control-character primitives from the path
bytes. With ``json.dump(payload, f, ensure_ascii=False)`` (line 776)
those bytes survive as raw UTF-8 in the committed JSON file.

The Markdown sink (``render_feed_health_markdown`` line 593) and the
GitHub-Issue body (``_GithubIssueReporter._build_body`` line 1131)
both correctly route ``feed_path`` through
:func:`src.utils.text.safe_markdown_codespan` (the canonical inline-
code-span helper that strips the canonical floor + collapses
whitespace + replaces backticks + caps length). The companion JSON
sink at line 759 was left as the structural drift — every sibling
sanitiser in the codebase covers the canonical floor at this exact
shape (provider-controlled / env-controlled string flowing into a
public artefact via ``ensure_ascii=False`` JSON dump), but this one
sink emitted the bytes raw.

Sinks (public artefacts)
------------------------

  * ``docs/feed-health.json`` — committed by ``update-cycle.yml`` on
    every cron tick. Served as a static asset on GitHub Pages
    (``https://origamihase.github.io/wien-oepnv/feed-health.json``)
    and via the raw.githubusercontent.com mirror. Consumed by:
      - LLM-driven downstream services (RSS-to-prompt pipelines,
        auto-summarisers) that ingest the JSON directly — Tag-block
        bytes survive as zero-width steganography primitives that the
        LLM honours but the human reviewer cannot see.
      - SIEMs / log shippers (Datadog / Splunk HEC) ingesting the
        JSON as structured events — embedded BiDi / 8-bit C1 / line-
        terminator bytes corrupt the structured-log envelope.
      - Operator dashboards rendering the JSON value in a UI — BiDi
        RLO inverts displayed text after the mark.
      - ``cat`` / ``less`` / GitHub web UI / IDE preview — terminal
        escape sequences (``\\x1b[``) and 8-bit CSI (``\\x9b``)
        trigger SGR commands on terminals that honour them.

Attack shape
------------

A hostile env override (intentional misconfig, leaked CI env,
compromised secret store, malicious workflow PR) sets ``OUT_PATH``
to a path containing the canonical attack-byte primitives:

  * **BiDi RLO (U+202E)** — inverts displayed text after the mark in
    every Unicode-aware UI that renders the JSON value.
  * **Tag block (U+E0000..U+E007F)** — the "ChatGPT invisible-
    instruction smuggling" primitive (2024 OpenAI disclosure).
    Every printable ASCII codepoint has a paired Tag character
    rendering as zero-width.
  * **Variation Selectors (U+FE00..U+FE0F, U+E0100..U+E01EF)** —
    4-bit-payload steganography primitive.
  * **Zero-width family (U+200B..U+200D), BOM (U+FEFF), ALM (U+061C),
    LRM/RLM, BiDi formatting (U+202A..U+202E), BiDi isolates
    (U+2066..U+2069), WJ/Invisible-Math (U+2060..U+2064)** — visual
    deception + cache-key collision primitives.
  * **SOFT HYPHEN (U+00AD)** + Cf-class Format characters
    (U+0600..U+0605, U+06DD, U+070F, U+0890..U+0891, U+08E2,
    U+206A..U+206F, U+FFF9..U+FFFB, U+110BD/U+110CD,
    U+13430..U+13438, U+1BCA0..U+1BCA3, U+1D173..U+1D17A) —
    invisible characters that pollute byte-equality / hash / GUID
    dedup keys; SOFT HYPHEN is the canonical "invisible-by-default"
    real-world IDN / package-name spoofing primitive (CVE-2018-19165
    / CVE-2021-43616).
  * **ASCII C0 controls (\\x00-\\x08, \\x0b, \\x0c, \\x0e-\\x1f)
    and C1 controls + DEL (\\x7f-\\x9f)** — terminal-escape /
    log-injection primitives. ``\\x9b...m`` is the 8-bit CSI form
    of ``\\x1b[...m`` honoured by xterm with eightBitInput, BSD
    consoles, rxvt in 8-bit mode.

Severity: MEDIUM. Visual deception + steganographic data smuggling +
LLM prompt-injection smuggling + cache-key / GUID collision primitive
on a public-served, machine-consumed artefact. No JS execution.

The fix
-------

Route ``report.feed_path`` through the canonical
``_CONTROL_CHARS_RE`` strip (already imported into the module and
applied to ``summary.dedupe_key`` / ``summary.titles`` at lines
739/741) before assigning into the JSON payload — same shape as the
existing duplicate-summary defence the prior round established. The
sanitiser preserves legitimate ASCII path bytes and German content
(umlauts ä/ö/ü/Ä/Ö/Ü + sharp s ß) intact while removing the
canonical attack-byte union.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from src.feed.reporting import (
    FeedHealthMetrics,
    RunReport,
    build_feed_health_payload,
)


# Sentinel marker shared by every PoC site so a future grep for
# ``SENTINEL_FEED_HEALTH_JSON_FEED_PATH_DRIFT`` finds the full call-graph.
SENTINEL_FEED_HEALTH_JSON_FEED_PATH_DRIFT = (
    "feed-health.json run.feed_path canonical-floor drift"
)


def _empty_metrics() -> FeedHealthMetrics:
    """Helper: minimal ``FeedHealthMetrics`` for the end-to-end PoCs.

    The attack-byte ride is on the ``RunReport.feed_path`` channel,
    not the metrics channel, so empty-zero metrics are sufficient
    for the JSON sink reproduction.
    """
    return FeedHealthMetrics(
        raw_items=0,
        filtered_items=0,
        deduped_items=0,
        new_items=0,
        duplicate_count=0,
        duplicates=(),
    )


def _make_report(feed_path: str) -> RunReport:
    """Build a finished ``RunReport`` whose ``feed_path`` carries
    the supplied attack-byte payload.

    ``RunReport.finish`` accepts a ``Path`` and stores
    ``feed_path.as_posix()`` on the report. A POSIX path may carry
    arbitrary bytes (POSIX disallows only ``/`` and NUL ``\\0``);
    every other code point in the canonical floor survives the
    constructor unmodified.
    """
    report = RunReport(statuses=[("wl", True)])
    report.provider_success("wl", items=0)
    report.finish(build_successful=True, feed_path=Path(feed_path))
    return report


# Canonical attack-byte inventory mirrored from the prior round. Each
# item is one (or a few) code point(s) in the canonical-floor union
# (``_INVISIBLE_DANGEROUS_RE`` / ``_CONTROL_CHARS_RE``). The labels
# match the journal's "canonical attack-byte inventory" so a future
# grep across the test suite finds the same names.
_CANONICAL_FLOOR_PRIMITIVES: tuple[tuple[str, str], ...] = (
    # ASCII C0 controls (excluding TAB/LF/CR which `clean_message`
    # collapses to a single space — those land in `feed_path` as
    # whitespace pre-fix, and the JSON `as_posix` form preserves them).
    ("\x00", "U+0000 NUL"),
    ("\x07", "U+0007 BEL (terminal-bell)"),
    ("\x08", "U+0008 BS"),
    ("\x0b", "U+000B VT"),
    ("\x0c", "U+000C FF (form-feed wipe)"),
    ("\x1b", "U+001B ESC (ANSI prefix)"),
    ("\x1f", "U+001F US"),
    # 8-bit C1 / DEL — 8-bit terminal-escape primitives that bypass
    # the 7-bit ``_ANSI_ESCAPE_RE`` defence on terminals that honour
    # them (xterm with eightBitInput, BSD consoles, rxvt 8-bit mode).
    ("\x7f", "U+007F DEL"),
    ("\x90", "U+0090 DCS (8-bit ESC P)"),
    ("\x9b", "U+009B CSI (8-bit ESC [)"),
    ("\x9d", "U+009D OSC (8-bit ESC ])"),
    # SOFT HYPHEN — the canonical "invisible-by-default" Cf code point
    # used in real-world spoofing attacks since 2018 (CVE-2018-19165
    # IDN homographs, CVE-2021-43616 npm package-name spoofing).
    ("­", "U+00AD SOFT HYPHEN"),
    # Arabic prefix Format band (Cf class, zero-width per UAX #9).
    ("؀", "U+0600 ARABIC NUMBER SIGN"),
    ("؜", "U+061C ARABIC LETTER MARK (BiDi)"),
    # MONGOLIAN VOWEL SEPARATOR (legacy Cf, defeats `\u200x`-only filters).
    ("᠎", "U+180E MONGOLIAN VOWEL SEPARATOR"),
    # Zero-width family + BiDi marks + LRM/RLM (CVE-2021-42574 first half).
    ("​", "U+200B ZERO WIDTH SPACE"),
    ("‌", "U+200C ZERO WIDTH NON-JOINER"),
    ("‍", "U+200D ZERO WIDTH JOINER"),
    ("‎", "U+200E LEFT-TO-RIGHT MARK"),
    ("‏", "U+200F RIGHT-TO-LEFT MARK"),
    # Unicode line / paragraph separators (record-terminator forging).
    (" ", "U+2028 LINE SEPARATOR"),
    (" ", "U+2029 PARAGRAPH SEPARATOR"),
    # CVE-2021-42574 BiDi formatting controls (LRE/RLE/PDF/LRO/RLO).
    ("‪", "U+202A LRE"),
    ("‫", "U+202B RLE"),
    ("‬", "U+202C PDF"),
    ("‭", "U+202D LRO"),
    ("‮", "U+202E RIGHT-TO-LEFT OVERRIDE"),
    # WORD JOINER + Invisible mathematical operators (steganography
    # alphabet — combinations encode arbitrary bytes that survive
    # copy-paste from the rendered JSON into an LLM context window).
    ("⁠", "U+2060 WORD JOINER"),
    ("⁡", "U+2061 FUNCTION APPLICATION"),
    ("⁢", "U+2062 INVISIBLE TIMES"),
    ("⁣", "U+2063 INVISIBLE SEPARATOR"),
    ("⁤", "U+2064 INVISIBLE PLUS"),
    # CVE-2021-42574 BiDi isolates (LRI/RLI/FSI/PDI — second half).
    ("⁦", "U+2066 LRI"),
    ("⁧", "U+2067 RLI"),
    ("⁨", "U+2068 FSI"),
    ("⁩", "U+2069 PDI"),
    # Variation Selectors (BMP, 4-bit-payload steganography).
    ("️", "U+FE0F VARIATION SELECTOR-16"),
    # BOM / ZWNBSP — visually invisible cache-key poisoner.
    ("﻿", "U+FEFF BOM"),
    # CJK Interlinear Annotation (zero-width except in dedicated
    # ruby renderers, none of which is in the Vienna ÖPNV pipeline).
    ("￹", "U+FFF9 INTERLINEAR ANNOTATION ANCHOR"),
    # Tag block — the canonical ChatGPT invisible-instruction smuggling
    # primitive (2024 OpenAI disclosure). U+E0020 = paired Tag form
    # of the ASCII SPACE.
    ("\U000e0020", "U+E0020 Unicode Tag SPACE"),
    ("\U000e0041", "U+E0041 Unicode Tag LATIN A"),
    # Supplementary Variation Selectors (plane 14).
    ("\U000e0100", "U+E0100 VARIATION SELECTOR-17"),
)


# ---------------------------------------------------------------------------
# (1) Per-primitive PoC: each canonical-floor attack byte planted in
#     ``OUT_PATH`` flows verbatim through the JSON sink pre-fix.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("primitive,label", _CANONICAL_FLOOR_PRIMITIVES)
def test_feed_health_json_feed_path_drops_canonical_floor_primitives(
    primitive: str, label: str,
) -> None:
    """A poisoned ``OUT_PATH`` carrying any canonical-floor primitive
    MUST NOT survive into the published ``docs/feed-health.json``
    ``run.feed_path`` field.

    Pre-fix the bytes flow through verbatim because
    ``build_feed_health_payload`` emits ``report.feed_path`` raw.
    Post-fix the canonical ``_CONTROL_CHARS_RE`` strip removes them
    at the JSON-sink boundary, mirroring the existing defence at the
    duplicate-summary fields (``dedupe_key`` / ``titles``) and the
    Markdown-sink defence (``safe_markdown_codespan(report.feed_path)``).
    """
    payload_path = f"docs/feed{primitive}evil.xml"
    report = _make_report(feed_path=payload_path)

    payload = build_feed_health_payload(report, _empty_metrics())

    feed_path_value = payload["run"]["feed_path"]
    assert isinstance(feed_path_value, str), (
        "build_feed_health_payload produced a non-string feed_path: "
        f"{feed_path_value!r}"
    )
    assert primitive not in feed_path_value, (
        f"{label} ({hex(ord(primitive))}) leaked into the published "
        f"feed-health.json run.feed_path field — payload: "
        f"{feed_path_value!r}"
    )

    # End-to-end: the same byte MUST NOT survive the json.dumps with
    # ``ensure_ascii=False`` either. Mirrors the existing C1 / Cf /
    # zero-width drift PoCs that pin the same shape at the dump level.
    rendered = json.dumps(payload, ensure_ascii=False)
    assert primitive not in rendered, (
        f"{label} ({hex(ord(primitive))}) leaked into the rendered "
        f"feed-health.json bytes — rendered: {rendered!r}"
    )


# ---------------------------------------------------------------------------
# (2) End-to-end: a multi-primitive payload landing all canonical-floor
#     bytes in ``OUT_PATH`` at once is fully scrubbed from the JSON sink.
# ---------------------------------------------------------------------------


def test_feed_health_json_feed_path_strips_combined_canonical_payload() -> None:
    """A single ``OUT_PATH`` carrying every canonical-floor primitive
    at once gets fully scrubbed in the JSON sink.

    Defence-in-depth shape: even if a future fix narrowed the scrubber
    by accident (e.g. dropped one band from the canonical-floor union)
    this test fails on the entire payload, surfacing the regression
    immediately rather than waiting for a per-primitive PoC to fire.
    """
    payload_path = "docs/feed" + "".join(
        primitive for primitive, _label in _CANONICAL_FLOOR_PRIMITIVES
    ) + "evil.xml"

    report = _make_report(feed_path=payload_path)
    payload = build_feed_health_payload(report, _empty_metrics())
    rendered = json.dumps(payload, ensure_ascii=False)

    for primitive, label in _CANONICAL_FLOOR_PRIMITIVES:
        assert primitive not in rendered, (
            f"{label} ({hex(ord(primitive))}) leaked into the rendered "
            f"feed-health.json bytes — combined payload PoC: {rendered!r}"
        )


# ---------------------------------------------------------------------------
# (3) Legitimate-content invariant: a normal POSIX path with German
#     content survives the scrubber unchanged. The fix is additive only
#     against the canonical-floor union; ASCII path bytes + safe
#     non-ASCII (umlauts, sharp s, emoji) MUST pass through verbatim.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "legitimate_path",
    [
        "docs/feed.xml",
        "docs/Wien-feed.xml",
        "docs/Bahnhöfe.xml",
        "docs/Müller-Straße/feed.xml",
        "docs/österreich/wien/feed.xml",
        "docs/feed-中文.xml",  # Chinese characters survive
        "docs/feed-🚆.xml",    # Train emoji survives
    ],
)
def test_feed_health_json_feed_path_preserves_legitimate_content(
    legitimate_path: str,
) -> None:
    """Legitimate POSIX paths with German / CJK / emoji content survive
    the canonical-floor scrubber unchanged. The fix is additive only —
    it must not eat valid Unicode that has no Trojan-Source value.
    """
    report = _make_report(feed_path=legitimate_path)
    payload = build_feed_health_payload(report, _empty_metrics())
    assert payload["run"]["feed_path"] == legitimate_path, (
        f"Legitimate path {legitimate_path!r} was modified by the JSON "
        f"sink scrubber. Post-fix: scrubber must be additive-only."
    )


# ---------------------------------------------------------------------------
# (4) Null-safety invariant: a None ``feed_path`` (the dataclass default
#     when ``RunReport.finish`` was never called with a path) MUST NOT
#     crash the scrubber. Pre-fix the JSON sink emitted None unmodified;
#     post-fix the scrubber must preserve the None.
# ---------------------------------------------------------------------------


def test_feed_health_json_feed_path_preserves_none() -> None:
    """A ``RunReport`` with no feed_path emits ``run.feed_path == None``
    in the JSON. The scrubber MUST handle the None case without
    crashing or coercing to ``"None"``.
    """
    report = RunReport(statuses=[("wl", True)])
    report.provider_success("wl", items=0)
    report.finish(build_successful=True)

    payload = build_feed_health_payload(report, _empty_metrics())
    assert payload["run"]["feed_path"] is None, (
        "JSON sink should preserve None feed_path verbatim; the scrubber "
        "must short-circuit on None rather than coerce to a string."
    )


# ---------------------------------------------------------------------------
# (5) Inventory invariant: the source of ``build_feed_health_payload``
#     MUST reference the canonical sanitiser for ``feed_path``.
#
#     A future refactor that re-introduces a raw ``report.feed_path``
#     emission into the payload (e.g. a new sub-field that bypasses the
#     scrubber) fails this test on the source-grep level — surfacing the
#     drift at PR-review time rather than during incident response.
# ---------------------------------------------------------------------------


def test_inventory_build_feed_health_payload_sanitises_feed_path() -> None:
    """The ``build_feed_health_payload`` source MUST sanitise
    ``feed_path`` through the canonical-floor regex before emitting
    it into the JSON payload. Mirrors the inventory-test pattern
    pinned for the duplicate-summary scrub.
    """
    source = inspect.getsource(build_feed_health_payload)
    # The fix MUST reference both the canonical sanitiser and the
    # ``feed_path`` field name so any source-grep reviewer finds the
    # defence at the same boundary as the duplicate-summary scrub.
    assert "_CONTROL_CHARS_RE" in source, (
        "build_feed_health_payload must reference the canonical "
        "_CONTROL_CHARS_RE sanitiser to scrub feed_path before emit."
    )
    # The fix MUST also keep the duplicate-summary scrub intact —
    # this is the inventory invariant pinned by the prior round and
    # we MUST NOT regress on it while adding the new scrub.
    assert "dedupe_key" in source and "titles" in source, (
        "build_feed_health_payload must continue to scrub the "
        "duplicate-summary fields established by the prior round."
    )
