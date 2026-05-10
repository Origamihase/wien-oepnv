"""Sentinel PoC: Channel-level ``<title>`` and ``<description>`` elements
in the published RSS feed are not sanitised for BiDi / zero-width /
control characters — Trojan-Source primitive on the published feed.

Threat model
------------

The pre-fix flow in ``src/build_feed.py:_make_rss`` writes the env-
controlled feed metadata directly into the channel-level RSS XML:

    ET.SubElement(channel, "title").text = feed_config.FEED_TITLE
    ET.SubElement(channel, "description").text = feed_config.FEED_DESC

Both ``feed_config.FEED_TITLE`` and ``feed_config.FEED_DESC`` are read
verbatim from the ``FEED_TITLE`` / ``FEED_DESC`` environment variables
at module-load time (``src/feed/config.py:_load_from_env``):

    FEED_TITLE = os.getenv("FEED_TITLE", DEFAULT_FEED_TITLE)
    FEED_DESC  = os.getenv("FEED_DESC",  DEFAULT_FEED_DESCRIPTION)

No control-character / BiDi / zero-width strip is applied. An env
override containing CVE-2021-42574 Trojan-Source primitives flows
verbatim into the published RSS XML's channel-level metadata.

The 2026-05-10 *Trojan-Source RSS via build_feed `_CONTROL_RE` drift*
family closed the same shape for the per-item sinks across seven
rounds:

* Round 1-5 (PR #1413): per-item ``<title>`` / ``<description>`` /
  time-line sinks via ``_sanitize_text(s) = _CONTROL_RE.sub("", s)``.
* Round 6 (PR #1418): widened ``_CONTROL_RE`` to the canonical
  ``_INVISIBLE_DANGEROUS_RE`` floor (BiDi + zero-width + LSEP/PSEP +
  C1 + DEL + BOM).
* Round 7 (PR #1420): per-item ``<guid>`` sink via the same helper.

The closing-checklist of Round 7 explicitly walked every per-item RSS
element emission in ``_emit_item`` (``title``, ``link``, ``guid``,
``pubDate``, ``description``, ``content:encoded``, ``<ext:first_seen>``,
``<ext:starts_at>``, ``<ext:ends_at>``) — but the audit walker did NOT
extend to the channel-level metadata in ``_make_rss``. This PoC
documents and pins the channel-level sibling.

The published ``docs/feed.xml`` artefact is fetched from
``https://origamihase.github.io/wien-oepnv/feed.xml`` by:

* Every subscriber's RSS reader (Feedly, NetNewsWire, Inoreader,
  FreshRSS, Vivaldi RSS, …). The channel ``<title>`` is displayed
  PROMINENTLY in the feed list (often the first thing a subscriber
  sees when adding the feed); ``<description>`` is used for
  feed-discovery metadata and shown in feed-management UIs.
* Every operator-facing forensic tool (XML viewers, IDE inspectors,
  GitHub's online file viewer, ``cat docs/feed.xml`` on a TTY).

When the env-controlled FEED_TITLE / FEED_DESC contains:

* **U+202E (RLO) — Right-to-Left Override.** A ``FEED_TITLE`` like
  ``Wien ÖPNV ‮evil-channel-title`` renders as
  ``Wien ÖPNV eltit-lennahc-live`` in any Unicode-aware viewer — the
  documented CVE-2021-42574 "Trojan Source" primitive on the channel
  metadata of a public artefact. Every subscriber's RSS reader
  displays the inverted title.
* **U+200B-U+200D (ZWSP/ZWNJ/ZWJ).** Zero-width chars create cache-
  key collisions and Unicode-equality disagreements in subscriber
  readers' dedup logic — an attacker churning the FEED_TITLE between
  visually-identical variants triggers re-fetch / re-import on every
  subscriber update.
* **U+2028 / U+2029 (LSEP/PSEP).** Line/paragraph separators that
  several Unicode-aware RSS parsers honour as line breaks, splitting
  the channel title/description into multiple visual lines or
  breaking the XML element boundary in a SIEM splitter.
* **U+FEFF (BOM).** Byte Order Mark inside a channel-level field
  causes parser divergence; some parsers skip it, others store it,
  leading to identifier mismatch between subscribers' caches and
  fresh fetches.
* **\\x80-\\x9F (8-bit C1 controls).** Per ECMA-48 / ISO 6429 these
  are 8-bit equivalents of the 7-bit ANSI escapes; ``\\x9B`` is
  8-bit CSI (``cat docs/feed.xml`` on xterm with ``eightBitInput``
  triggers attacker-controlled colour / cursor-move sequences).
* **\\x00-\\x08, \\x0B-\\x0C, \\x0E-\\x1F, \\x7F.** Most are invalid
  in XML 1.0; downstream RSS parsers may emit parse exceptions on
  these bytes, breaking feed availability for affected subscribers.

The env-override surface is realistic: a leaked CI env (the project
owner's GitHub Actions secrets), a compromised secret store, an
intentional misconfig copy-pasted from old documentation, or a
typo that introduces an invisible BiDi mark all reach this code
path.

Severity
--------

LOW-MEDIUM — Trojan-Source primitive on a public artefact, contingent
on env-override behaviour. No current vulnerability surface (the
default ``DEFAULT_FEED_TITLE`` / ``DEFAULT_FEED_DESCRIPTION`` carry no
control characters) but a structural drift candidate that mirrors the
exact shape closed for every per-item RSS sink across the
*Trojan-Source RSS Drift* family. Closes the channel-level sibling
that Rounds 1-7 of the family did not enumerate.

Fix shape
---------

Apply ``_sanitize_text`` at the channel-emit site in ``_make_rss``,
mirroring the per-item sinks already routed through the helper.
Two-line change inside ``src/build_feed.py:_make_rss``:

    ET.SubElement(channel, "title").text = _sanitize_text(feed_config.FEED_TITLE)
    ET.SubElement(channel, "description").text = _sanitize_text(feed_config.FEED_DESC)

The fix is **additive-only**: every legitimate FEED_TITLE / FEED_DESC
value (no BiDi / control / zero-width chars) is unchanged post-fix.
Sanitising at the publishing surface (channel-emit site) rather than
at config-load time mirrors the per-item shape and avoids a
circular import between ``src/feed/config.py`` and
``src/build_feed.py``.

Inventory invariant
-------------------

The closing-checklist for the *Trojan-Source RSS Drift* family is
amended to walk EVERY RSS element emission — both per-item
(``_emit_item``) and channel-level (``_make_rss``) — and assert each
upstream-controlled OR env-controlled text-typed field routes through
``_sanitize_text``. The inventory test
``test_inventory_no_dangerous_chars_in_channel_title_desc`` plants
every canonical dangerous char into FEED_TITLE / FEED_DESC and
asserts the rendered RSS XML carries none of them. A future PR that
re-introduces a channel-level metadata path bypassing
``_sanitize_text`` (e.g. a new channel-level element emitted directly
from an env var) fails the test at PR-review time.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable, Iterator

import pytest
from defusedxml import ElementTree as ET

from src import build_feed
from src.feed import config as feed_config


_NOW = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)


# Canonical Trojan-Source / control character set the post-fix channel
# emission MUST strip. Mirrors the set in
# ``tests/test_sentinel_guid_trojan_source.py`` and the canonical
# ``src/build_feed.py:_CONTROL_RE`` regex.
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
    ("\x9b", "C1 CSI (Control Sequence Introducer)"),
    ("\x9d", "C1 OSC (Operating System Command)"),
    ("\x9f", "C1 APC (Application Program Command)"),
    # ASCII C0 control + DEL
    ("\x01", "SOH"),
    ("\x1b", "ESC"),
    ("\x1f", "US"),
    ("\x7f", "DEL"),
)


@pytest.fixture
def channel_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[[str, str], str]]:
    """Set FEED_TITLE / FEED_DESC via env, refresh the feed config, and
    return a helper that renders an empty-item RSS feed and yields the
    XML string. Restores the config at teardown so subsequent tests see
    the default metadata."""

    def _render(title: str, description: str) -> str:
        monkeypatch.setenv("FEED_TITLE", title)
        monkeypatch.setenv("FEED_DESC", description)
        feed_config.refresh_from_env()
        return build_feed._make_rss([], _NOW, {})

    yield _render
    monkeypatch.delenv("FEED_TITLE", raising=False)
    monkeypatch.delenv("FEED_DESC", raising=False)
    feed_config.refresh_from_env()


# ---------------------------------------------------------------------------
# (1) Per-code-point coverage — channel ``<title>``.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code_point,label", CANONICAL_DANGEROUS_CHARS)
def test_channel_title_strips_canonical_dangerous_chars(
    code_point: str,
    label: str,
    channel_metadata: Callable[[str, str], str],
) -> None:
    """Pre-fix: every canonical Trojan-Source / control character in
    the env-controlled ``FEED_TITLE`` flows verbatim into the channel-
    level ``<title>`` element of the published RSS XML. Post-fix:
    ``_sanitize_text`` strips the canonical ``_CONTROL_RE`` set so the
    channel metadata never carries the documented Trojan-Source
    primitives.

    Closes the channel-level sibling of the per-item Trojan-Source RSS
    Drift family — the audit walker for Round 7 (PR #1420) enumerated
    every per-item RSS element but did NOT extend to the channel-level
    metadata in ``_make_rss``.
    """
    title = f"Wien ÖPNV{code_point}prefix-suffix"
    xml = channel_metadata(title, "Default description")

    assert code_point not in xml, (
        f"Trojan-Source / control char {label} (U+{ord(code_point):04X}) "
        f"survived in published RSS XML's channel <title> element. This "
        f"is a documented Trojan-Source primitive on a public artefact "
        f"reaching every subscriber's RSS reader."
    )


# ---------------------------------------------------------------------------
# (2) Per-code-point coverage — channel ``<description>``.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code_point,label", CANONICAL_DANGEROUS_CHARS)
def test_channel_description_strips_canonical_dangerous_chars(
    code_point: str,
    label: str,
    channel_metadata: Callable[[str, str], str],
) -> None:
    """Pre-fix: every canonical Trojan-Source / control character in
    the env-controlled ``FEED_DESC`` flows verbatim into the channel-
    level ``<description>`` element of the published RSS XML. Post-fix:
    ``_sanitize_text`` strips the canonical set so the channel metadata
    never carries the documented Trojan-Source primitives.

    Mirrors ``test_channel_title_strips_canonical_dangerous_chars`` for
    the parallel ``<description>`` channel field — both are env-
    controlled and route through the same emission shape in
    ``_make_rss``.
    """
    description = f"Disruptions{code_point}prefix-suffix"
    xml = channel_metadata("Default title", description)

    assert code_point not in xml, (
        f"Trojan-Source / control char {label} (U+{ord(code_point):04X}) "
        f"survived in published RSS XML's channel <description> element. "
        f"This is a documented Trojan-Source primitive on a public "
        f"artefact reaching every subscriber's RSS reader."
    )


# ---------------------------------------------------------------------------
# (3) Legitimate FEED_TITLE / FEED_DESC — happy path regression.
# ---------------------------------------------------------------------------


def test_channel_title_legitimate_value_preserved(
    channel_metadata: Callable[[str, str], str],
) -> None:
    """Regression: legitimate channel title text must continue to be
    preserved verbatim. Pre- and post-fix behaviour are identical for
    the legitimate case."""
    xml = channel_metadata("Wien ÖPNV Disruptions", "Default description")

    root = ET.fromstring(xml)
    channel = root.find("channel")
    assert channel is not None
    title = channel.find("title")
    assert title is not None
    assert title.text == "Wien ÖPNV Disruptions"


def test_channel_description_legitimate_value_preserved(
    channel_metadata: Callable[[str, str], str],
) -> None:
    """Regression: legitimate channel description text must continue
    to be preserved verbatim, including German umlauts and other
    legitimate Unicode letters."""
    xml = channel_metadata(
        "Default title",
        "Aktuelle Störungsmeldungen für Wiener Linien, ÖBB und VOR.",
    )

    root = ET.fromstring(xml)
    channel = root.find("channel")
    assert channel is not None
    description = channel.find("description")
    assert description is not None
    assert (
        description.text
        == "Aktuelle Störungsmeldungen für Wiener Linien, ÖBB und VOR."
    )


# ---------------------------------------------------------------------------
# (4) End-to-end Trojan-Source PoC — RLO mark in FEED_TITLE renders
#     reversed text in any Unicode-aware RSS reader / XML viewer.
# ---------------------------------------------------------------------------


def test_channel_title_rlo_trojan_source_published_feed_xml(
    channel_metadata: Callable[[str, str], str],
) -> None:
    """End-to-end Trojan-Source PoC: an env-controlled FEED_TITLE
    carrying U+202E (RLO) renders inverted text in any Unicode-aware
    RSS reader (Feedly, NetNewsWire, Inoreader, FreshRSS, Vivaldi RSS),
    XML viewer (IDE inspectors, GitHub Pages preview), or terminal
    (``cat docs/feed.xml`` on a UTF-8 TTY).

    Pre-fix the RLO byte lands inside ``<title>`` of ``docs/feed.xml``
    and every subscriber's reader displays the post-RLO segment
    visually reversed. Post-fix, the RLO is stripped at emit time so
    the published artefact carries no Trojan-Source primitive."""
    payload_title = "Wien ÖPNV ‮evil-channel-title"
    xml = channel_metadata(payload_title, "Default description")

    assert "‮" not in xml, (
        "U+202E (RLO) survived in published RSS XML's channel <title> "
        "after fix; subscribers' Unicode-aware readers would render the "
        "post-RLO segment inverted (CVE-2021-42574 Trojan Source)."
    )


def test_channel_description_zwsp_dedup_evasion_poc(
    channel_metadata: Callable[[str, str], str],
) -> None:
    """End-to-end zero-width PoC: an env-controlled FEED_DESC carrying
    U+200B (ZWSP) creates cache-key collisions / dedup-evasion in
    Unicode-aware subscriber readers — the same shape that motivated
    the per-item sanitisation rounds. Post-fix, ZWSP is stripped at
    emit time so subscribers' caches are not poisoned by visually-
    identical-but-byte-different channel descriptions."""
    payload_desc = "Disruptions​‌‍ for Vienna transit"
    xml = channel_metadata("Default title", payload_desc)

    assert "​" not in xml
    assert "‌" not in xml
    assert "‍" not in xml


# ---------------------------------------------------------------------------
# (5) Inventory invariant — the rendered RSS feed must never carry any
#     canonical dangerous character in the channel-level title /
#     description, regardless of env-override content.
# ---------------------------------------------------------------------------


def test_inventory_no_dangerous_chars_in_channel_title_desc(
    channel_metadata: Callable[[str, str], str],
) -> None:
    """Pin the canonical-set coverage invariant for the channel-level
    metadata sinks programmatically. Plants every canonical dangerous
    char into a single FEED_TITLE / FEED_DESC pair and asserts the
    rendered RSS XML carries none of them.

    A future PR that re-introduces a channel-level metadata path
    bypassing ``_sanitize_text`` (e.g. a new env-controlled element
    emitted directly without the helper) fails this test at PR-review
    time — same shape as the per-item inventory test
    ``test_inventory_no_dangerous_chars_in_guid_element`` from
    ``tests/test_sentinel_guid_trojan_source.py``.
    """
    payload_chars = "".join(c for c, _ in CANONICAL_DANGEROUS_CHARS)
    title = f"prefix{payload_chars}suffix"
    description = f"desc-prefix{payload_chars}desc-suffix"
    xml = channel_metadata(title, description)

    for code_point, label in CANONICAL_DANGEROUS_CHARS:
        assert code_point not in xml, (
            f"Inventory invariant breach: {label} (U+{ord(code_point):04X}) "
            f"survived in the published RSS XML's channel-level metadata. "
            f"Every env-controlled channel-level field MUST route through "
            f"``_sanitize_text`` at the emit site in ``_make_rss``."
        )


# ---------------------------------------------------------------------------
# (6) Default values — sanity check that the test fixture restores
#     state cleanly so the canonical defaults round-trip unchanged.
# ---------------------------------------------------------------------------


def test_channel_metadata_default_round_trip(
    channel_metadata: Callable[[str, str], str],
) -> None:
    """Sanity check: the default FEED_TITLE / FEED_DESC values from
    ``src/config/defaults.py`` carry no control characters, so they
    round-trip unchanged through the post-fix sanitiser. Confirms the
    fix is genuinely additive-only and does not regress the default
    artefact."""
    from src.config.defaults import DEFAULT_FEED_DESCRIPTION, DEFAULT_FEED_TITLE

    xml = channel_metadata(DEFAULT_FEED_TITLE, DEFAULT_FEED_DESCRIPTION)

    root = ET.fromstring(xml)
    channel = root.find("channel")
    assert channel is not None
    title = channel.find("title")
    assert title is not None
    assert title.text == DEFAULT_FEED_TITLE
    description = channel.find("description")
    assert description is not None
    assert description.text == DEFAULT_FEED_DESCRIPTION
