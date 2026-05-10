"""Sentinel PoC: Per-item ``<guid>`` element in the published RSS feed
is not sanitised for BiDi / zero-width / control characters — Trojan-
Source primitive on the published feed.

Threat model
------------

The pre-fix flow in ``src/build_feed.py:_format_item_content`` applies
``_sanitize_text`` (which strips the canonical ``_CONTROL_RE`` set —
C0/C1 controls + DEL + BiDi format controls + zero-width chars + line
separators + BOM) to the **title** and **summary**. But the **guid**
is only ``str.strip()``-ed:

    raw_guid = it.get("guid") or ident
    guid = str(raw_guid).strip() if raw_guid is not None else ident

``str.strip()`` only removes ASCII whitespace from the EDGES; internal
control / BiDi / zero-width characters survive verbatim. The guid then
flows into the published RSS XML's ``<guid>...</guid>`` element via
``ET.SubElement(item, "guid").text = formatted.guid`` — XML
serialisation escapes ``<>&`` but does NOT strip Unicode BiDi marks,
zero-width chars, or U+202E (RLO).

The published ``docs/feed.xml`` is fetched by every subscriber's RSS
reader (Feedly, NetNewsWire, Inoreader, FreshRSS, …) and by every
operator-facing forensic tool (XML viewers, IDE inspectors, GitHub's
file viewer). When the guid contains:

* **U+202E (RLO)** — Right-to-Left Override. A guid like
  ``A‮B-disruption-12345`` renders as ``A54321-noitpursid-B`` in
  any Unicode-aware viewer — the documented CVE-2021-42574 "Trojan
  Source" primitive on a public artefact.
* **U+200B-U+200D (ZWSP/ZWNJ/ZWJ)** — Zero-width chars create cache-
  key collisions and equality-check disagreements; an attacker
  churning the dedup window with visually-identical guids floods the
  feed.
* **U+2028 / U+2029 (LSEP/PSEP)** — Line/paragraph separators that
  several Unicode-aware RSS parsers honour as line breaks, splitting
  a single guid into two records or breaking the XML element
  boundary in a SIEM splitter.
* **U+FEFF (BOM)** — Byte Order Mark inside a guid causes parser
  divergence; some parsers skip it, others store it, leading to
  identifier mismatch.
* **\\x00-\\x08, \\x0B-\\x0C, \\x0E-\\x1F, \\x7F-\\x9F** — Most are
  invalid in XML 1.0 and may cause parser exceptions in downstream
  RSS consumers, breaking feed availability for affected readers.

Per-item guids are *upstream-controlled* — the OEBB provider takes
``<guid>`` directly from the upstream RSS XML (verified at
``src/providers/oebb.py:_derive_guid`` line ~1450, which uses
``raw_guid`` if ≤128 chars), and similarly for WL/VOR/Baustellen
which use ``make_guid()`` based on upstream-controlled fields. A
compromised upstream / DNS-hijack / MITM (despite TLS, e.g. via
compromised CA or compromised CDN endpoint) can plant a malicious
guid that flows verbatim into the public feed.

Severity
--------

LOW-MEDIUM — Trojan-Source primitive on a public artefact, contingent
on upstream behaviour. No current vulnerability surface (every
upstream-supplied guid in ``cache/*/events.json`` is
HTTPS-URL-shaped with no BiDi / control chars) but a structural
drift candidate that mirrors the exact shape closed by:

* 2026-05-10 *Trojan-Source RSS via _CONTROL_RE drift* (PR #1413) —
  closed for the title / description / time-line sinks.
* 2026-05-10 *CSV Formula-Injection Bypass* (PR #1412) — closed for
  the CSV writer.
* 2026-05-10 *8-bit C1 Terminal-Escape Drift* (PR #1414) — widened
  the canonical floor.

The guid is the LAST per-item RSS sink that still routes through a
``str.strip()``-only path instead of the canonical ``_sanitize_text``
helper.

Fix shape
---------

Apply ``_sanitize_text`` to the guid before assignment, mirroring
the title / description sanitisation that already happens in
``_format_item_content``. Single-line change, no new helper, no API
impact. The fix is **additive-only**: every legitimate guid in the
live cache (HTTPS-URL-shaped, no BiDi / control chars) is unchanged
post-fix.
"""

from __future__ import annotations

import datetime
from typing import Any

from defusedxml import ElementTree as ET

from src import build_feed


def _emit_item_str(
    item: Any, now: datetime.datetime, state: dict[str, Any]
) -> tuple[str, str]:
    """Mirror the helper from ``test_link_sanitization.py`` so the
    PoC tests use the canonical XML-emit contract."""
    ident, elem, replacements = build_feed._emit_item(item, now, state)
    xml_str = ET.tostring(elem, encoding="unicode")
    for ph, content in replacements.items():
        xml_str = xml_str.replace(ph, content)
    return ident, xml_str


# ---------------------------------------------------------------------------
# Per-code-point coverage — the canonical Trojan-Source / control
# character set must be stripped from the guid.
# ---------------------------------------------------------------------------


CANONICAL_DANGEROUS_CHARS: tuple[tuple[str, str], ...] = (
    # CVE-2021-42574 BiDi Trojan-Source primitives
    ("‮", "RLO (Right-to-Left Override)"),
    ("‭", "LRO (Left-to-Right Override)"),
    ("‪", "LRE (Left-to-Right Embedding)"),
    ("‫", "RLE (Right-to-Left Embedding)"),
    ("‬", "PDF (Pop Directional Formatting)"),
    ("⁦", "LRI (Left-to-Right Isolate)"),
    ("⁧", "RLI (Right-to-Left Isolate)"),
    ("⁨", "FSI (First Strong Isolate)"),
    ("⁩", "PDI (Pop Directional Isolate)"),
    # Zero-width chars
    ("​", "ZWSP (Zero-Width Space)"),
    ("‌", "ZWNJ (Zero-Width Non-Joiner)"),
    ("‍", "ZWJ (Zero-Width Joiner)"),
    ("‎", "LRM (Left-to-Right Mark)"),
    ("‏", "RLM (Right-to-Left Mark)"),
    ("؜", "ALM (Arabic Letter Mark)"),
    ("﻿", "BOM (Byte Order Mark / ZWNBSP)"),
    # Line/paragraph separators
    (" ", "LSEP (Line Separator)"),
    (" ", "PSEP (Paragraph Separator)"),
    # 8-bit C1 controls
    ("\x80", "C1 PAD"),
    ("\x9B", "C1 CSI (Control Sequence Introducer)"),
    ("\x9D", "C1 OSC (Operating System Command)"),
    ("\x9F", "C1 APC (Application Program Command)"),
    # ASCII C0 control + DEL
    ("\x01", "SOH"),
    ("\x1B", "ESC"),
    ("\x1F", "US"),
    ("\x7F", "DEL"),
)


# ---------------------------------------------------------------------------
# (1) Per-code-point Trojan-Source / control char strip.
# ---------------------------------------------------------------------------


def _make_item(now: datetime.datetime, guid: str) -> dict[str, Any]:
    """Build a minimal RSS item dict that exercises the GUID emission."""
    return {
        "title": "Test Item",
        "link": "https://example.com/disruption",
        "guid": guid,
        "pubDate": now,
        "description": "Description",
    }


def test_guid_strips_canonical_dangerous_chars() -> None:
    """Pre-fix: every canonical Trojan-Source / control character in the
    upstream-supplied guid flows verbatim into the published RSS XML's
    ``<guid>`` element (only ``str.strip()`` is applied, which strips
    ASCII whitespace from edges only). Post-fix: the
    ``_sanitize_text`` helper strips the canonical ``_CONTROL_RE`` set
    so the published feed never carries the documented Trojan-Source
    primitives.

    Closes the per-item ``<guid>`` Trojan-Source gap — the last RSS
    sink that still routed through ``str.strip()``-only instead of
    the canonical ``_sanitize_text`` helper used for title /
    description / time-line.
    """
    now = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)
    state: dict[str, dict[str, Any]] = {}

    for code_point, label in CANONICAL_DANGEROUS_CHARS:
        guid = f"valid-prefix{code_point}valid-suffix-12345"
        item = _make_item(now, guid)

        ident, xml = _emit_item_str(item, now, state)

        assert code_point not in xml, (
            f"Trojan-Source / control char {label} (U+{ord(code_point):04X}) "
            f"survived in published RSS XML's <guid> element. This is a "
            f"documented Trojan-Source primitive on a public artefact."
        )


# ---------------------------------------------------------------------------
# (2) Legitimate guids — happy path regression.
# ---------------------------------------------------------------------------


def test_guid_legitimate_ascii_preserved() -> None:
    """Regression: legitimate ASCII guids must continue to be preserved
    unchanged. Pre- and post-fix behaviour are identical for the
    legitimate case."""
    now = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)
    state: dict[str, dict[str, Any]] = {}

    item = _make_item(now, "abc-12345-disruption-key")
    ident, xml = _emit_item_str(item, now, state)
    assert "abc-12345-disruption-key" in xml


def test_guid_legitimate_https_url_preserved() -> None:
    """Regression: legitimate HTTPS URL guids (the OEBB upstream
    shape) must be preserved verbatim."""
    now = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)
    state: dict[str, dict[str, Any]] = {}

    guid = "https://fahrplan.oebb.at/bin/query.exe/dn?ujm=1&mapType=TRACKINFO&829791"
    item = _make_item(now, guid)
    ident, xml = _emit_item_str(item, now, state)
    # XML escaping turns & into &amp; — verify by unescaping:
    assert "https://fahrplan.oebb.at/bin/query.exe/dn?ujm=1" in xml.replace("&amp;", "&")


def test_guid_unicode_letters_preserved() -> None:
    """Regression: legitimate Unicode letters (German umlauts, etc.)
    in guids must be preserved — only the documented Trojan-Source /
    control set is stripped."""
    now = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)
    state: dict[str, dict[str, Any]] = {}

    item = _make_item(now, "Wien-Heiligenstadt-Bahnhof-Störung-12345")
    ident, xml = _emit_item_str(item, now, state)
    assert "Wien-Heiligenstadt-Bahnhof-Störung-12345" in xml


# ---------------------------------------------------------------------------
# (3) End-to-end Trojan-Source PoC — RLO mark on a guid renders
#     reversed text in Unicode-aware readers.
# ---------------------------------------------------------------------------


def test_guid_rlo_trojan_source_published_feed_xml() -> None:
    """End-to-end Trojan-Source PoC: an upstream-supplied guid carrying
    U+202E (RLO) renders inverted text in any Unicode-aware RSS reader
    or XML viewer that displays the guid (Feedly, NetNewsWire, GitHub
    Pages preview, IDE inspector). Post-fix, the RLO is stripped at
    the ``_sanitize_text`` boundary so the published feed never
    carries the inversion primitive."""
    now = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)
    state: dict[str, dict[str, Any]] = {}

    # The classic Trojan-Source payload: legitimate-looking prefix +
    # RLO + reversed suffix. In a Unicode-aware viewer this renders
    # as if the suffix is the prefix and vice versa — perfect
    # phishing primitive for any reader that displays the guid.
    item = _make_item(now, "click-here-‮evil-12345")
    ident, xml = _emit_item_str(item, now, state)

    assert "‮" not in xml, (
        "U+202E (RLO) Trojan-Source mark survived in published RSS "
        "XML's <guid> element after fix; this is a documented "
        "phishing primitive on every Unicode-aware RSS reader."
    )


# ---------------------------------------------------------------------------
# (4) Inventory invariant — no canonical Trojan-Source / control
#     character ever survives in the published <guid> element.
# ---------------------------------------------------------------------------


def test_inventory_no_dangerous_chars_in_guid_element() -> None:
    """Auto-discoverable inventory walker: planted upstream guids
    carrying every canonical Trojan-Source / control character must
    never survive the sanitisation. A future regression that
    re-introduces a path bypassing ``_sanitize_text`` for the guid
    fails this test at PR-review time."""
    now = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)
    state: dict[str, dict[str, Any]] = {}

    # Build a guid that contains EVERY canonical dangerous code point
    # — if even one survives, the test fails.
    payload_chars = "".join(cp for cp, _ in CANONICAL_DANGEROUS_CHARS)
    guid = f"safe-prefix{payload_chars}safe-suffix-12345"
    item = _make_item(now, guid)

    ident, xml = _emit_item_str(item, now, state)

    leaked: list[str] = []
    for code_point, label in CANONICAL_DANGEROUS_CHARS:
        if code_point in xml:
            leaked.append(f"{label} (U+{ord(code_point):04X})")

    assert not leaked, (
        f"Trojan-Source / control characters leaked into <guid>: {leaked}. "
        f"Apply ``_sanitize_text`` to the guid before XML emission."
    )
