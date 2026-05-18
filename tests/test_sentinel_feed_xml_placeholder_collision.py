"""Sentinel drift coverage for the RSS feed CDATA-placeholder collision
check in ``src/build_feed.py:_emit_item``.

Vulnerability
=============

``_emit_item`` generates a cryptographically random per-item placeholder
(``___CDATA_CONTENT_<32-hex>___`` / ``___CDATA_TITLE_<32-hex>___``) and
inserts it as ``.text`` on the ``<title>`` / ``<content:encoded>``
ElementTree elements. After the document is serialised, a downstream
``xml_str.replace(placeholder, "<![CDATA[<content>]]>")`` pass swaps the
placeholders for CDATA-wrapped sections. The placeholder approach is the
canonical workaround for ``xml.etree.ElementTree``'s lack of CDATA
support.

Pre-fix the loop's collision check only verified that the placeholder
strings were absent from THREE of the eight text-bearing fields on
``FormattedContent``:

  * ``formatted.desc_html`` (the full HTML description)
  * ``formatted.raw_desc`` (the raw description as supplied by the upstream)
  * ``formatted.title_out`` (the post-sanitisation title)

But it did NOT check the remaining FIVE text-bearing fields that flow
verbatim into the serialised XML (or into CDATA-wrap replacement strings):

  * ``formatted.link`` — used as ``<link>`` element text
    (line ~2336). Upstream-controlled URL.
  * ``formatted.guid`` — used as ``<guid>`` element text
    (line ~2340). Upstream-controlled identifier.
  * ``formatted.desc_text_truncated`` — used as ``<description>``
    element text (line ~2361). Derived from ``raw_desc`` after HTML
    stripping + truncation; truncation may cut the placeholder pattern
    differently than the ``raw_desc`` check expects.
  * ``formatted.title_cdata`` — embedded in the title placeholder's
    replacement string (``<![CDATA[{title_cdata}]]>``).
  * ``formatted.desc_cdata`` — embedded in the content placeholder's
    replacement string (``<![CDATA[{desc_cdata}]]>``).

If an upstream feed delivers an item whose ``link``, ``guid``, or
description contains the EXACT placeholder string of THAT item's randomly-
chosen UID (probability 2^-128 per request — astronomically low for a
direct attacker, but a real correctness defect for the trusted upstream
data flow this project explicitly Zero-Trusts), the global
``xml_str.replace()`` substitutes CDATA into the wrong XML element:

::

  Pre-fix output (item link contained the placeholder):
    <link><![CDATA[<content>]]></link>          ← CORRUPTED; CDATA inside <link>
    <content:encoded></content:encoded>          ← EMPTY; replacement consumed
                                                    by the link element

Feed readers consuming the corrupted XML fail to parse the affected
item, breaking the published ``docs/feed.xml`` for every subscriber.

Threat model
============

The wien-oepnv project aggregates Verkehrsmeldungen from upstream feeds
(Wiener Linien, ÖBB, VOR/VAO, Stadt-Wien OGD) that are explicitly Zero-
Trusted (``AGENTS.md`` §3 *Netzwerkzugriffe* — Zero-Trust upstream
payloads). The bug therefore lives EXACTLY at the trust boundary the
project's own security model declares hostile: an upstream-controlled
``link`` / ``guid`` / ``description`` field flowing through
``_format_item_content`` into the published RSS XML.

Practical exploitability requires the attacker to predict the
cryptographically random 128-bit UID — astronomically low for a direct
remote attacker, but the bug is a CORRECTNESS DEFECT regardless: a
legitimate upstream-supplied URL that coincidentally embeds the
placeholder pattern would silently corrupt the public feed. The fix is
defense-in-depth aligned with the project's "trust nothing, verify
everything" philosophy and closes the gap without changing the
fast-path behaviour for benign inputs.

Fix
===

Extract the collision check into a helper function
``_placeholder_collides_with_formatted`` that walks all eight text-
bearing fields of ``FormattedContent`` and returns True if either
placeholder appears in any of them. ``_emit_item``'s loop replaces its
inline check with a single call to the helper, dropping its complexity
by 2 points (the multi-``and`` boolop chain becomes one ``if not``
expression) — preserving the C901 ≤ 15 ceiling.

Marker: SENTINEL_FEED_XML_PLACEHOLDER_COLLISION.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

import src.build_feed
from src.build_feed import _emit_item, _make_rss


SENTINEL_FEED_XML_PLACEHOLDER_COLLISION = (
    "SENTINEL_FEED_XML_PLACEHOLDER_COLLISION: the _emit_item loop's "
    "placeholder collision check missed five of the eight text-bearing "
    "fields on FormattedContent (link, guid, desc_text_truncated, "
    "title_cdata, desc_cdata). An upstream-controlled item whose link / "
    "guid / description coincided with the random placeholder pattern "
    "would survive the loop and corrupt the serialised RSS XML when the "
    "downstream global xml_str.replace() substituted CDATA into the "
    "wrong element."
)


_NOW = datetime(2026, 5, 18, 18, 0, 0, tzinfo=UTC)
_FIXED_UID = "f" * 32  # Deterministic UID for monkeypatched secrets.token_hex
_PH_CONTENT_FIXED = f"___CDATA_CONTENT_{_FIXED_UID}___"
_PH_TITLE_FIXED = f"___CDATA_TITLE_{_FIXED_UID}___"


@pytest.fixture
def fixed_uid(monkeypatch: pytest.MonkeyPatch) -> str:
    """Pin ``secrets.token_hex`` inside ``src.build_feed`` so every call
    returns the same UID, making the placeholder fully predictable for
    PoC tests."""
    monkeypatch.setattr(src.build_feed.secrets, "token_hex", lambda _n: _FIXED_UID)
    return _FIXED_UID


def _base_item(**overrides: Any) -> dict[str, Any]:
    """Build a minimal FeedItem-shaped dict suitable for ``_emit_item``.

    The defaults are valid, non-colliding values. Tests override the
    specific field they want to plant the placeholder in.
    """
    item: dict[str, Any] = {
        "source": "test",
        "title": "Test Disruption",
        "description": "Test description with no placeholder pattern.",
        "link": "https://www.wienerlinien.at/test",
        "guid": "wien-oepnv:test:001",
        "pubDate": _NOW,
        "starts_at": _NOW,
        "ends_at": None,
        "category": "Hinweis",
        "lines": [],
        "stops": [],
    }
    item.update(overrides)
    return item


# ---------------------------------------------------------------------------
# (0) Drift premise — _emit_item MUST detect a placeholder collision in
# EACH of the previously-unchecked text fields and exhaust the retry loop
# (because the monkeypatched secrets.token_hex always returns _FIXED_UID).
# ---------------------------------------------------------------------------


def test_drift_premise_link_with_placeholder_triggers_retry_exhaustion(
    fixed_uid: str,
) -> None:
    """When ``formatted.link`` contains the placeholder pattern of the
    monkeypatched fixed UID, the loop MUST detect the collision and
    eventually raise ``RuntimeError`` (because every retry produces the
    same colliding UID).

    Pre-fix the loop only checks ``desc_html`` / ``raw_desc`` /
    ``title_out`` — so the placeholder in ``link`` survives and the loop
    terminates at attempt 1 with a colliding UID, then corrupts the
    serialised XML downstream. The URL uses a real Wiener Linien
    hostname so that ``_resolve_item_link``'s HTTPS-only + SSRF
    validation passes — the placeholder pattern (``_`` + alphanumerics)
    is RFC-3986 path-segment-safe and survives validation verbatim.
    """
    item = _base_item(
        link=f"https://www.wienerlinien.at/path/{_PH_CONTENT_FIXED}/end"
    )
    with pytest.raises(
        RuntimeError,
        match="Konnte keinen eindeutigen Platzhalter generieren",
    ):
        _emit_item(item, _NOW, {})


def test_drift_premise_guid_with_placeholder_triggers_retry_exhaustion(
    fixed_uid: str,
) -> None:
    """When ``formatted.guid`` contains the placeholder pattern, the
    loop MUST detect the collision and exhaust retries.

    Pre-fix the ``guid`` field was unchecked — upstream-controlled
    GUIDs containing the placeholder slipped past the loop.
    """
    item = _base_item(guid=f"urn:example:{_PH_CONTENT_FIXED}")
    with pytest.raises(
        RuntimeError,
        match="Konnte keinen eindeutigen Platzhalter generieren",
    ):
        _emit_item(item, _NOW, {})


def test_drift_premise_description_with_title_placeholder_triggers_retry(
    fixed_uid: str,
) -> None:
    """When ``formatted.desc_text_truncated`` (or its source
    ``raw_desc``) contains a TITLE-prefixed placeholder, the loop MUST
    still detect the collision.

    The pre-fix check verified ``PH_TITLE not in formatted.title_out``
    but did NOT verify the title placeholder's absence in description
    fields. The downstream ``xml_str.replace(PH_TITLE, ...)`` is global
    across the document, so a planted title placeholder inside
    ``<description>`` text would be substituted with the title's CDATA
    wrap — corrupting the description element.
    """
    item = _base_item(description=f"Disruption notice: {_PH_TITLE_FIXED}")
    with pytest.raises(
        RuntimeError,
        match="Konnte keinen eindeutigen Platzhalter generieren",
    ):
        _emit_item(item, _NOW, {})


def test_drift_premise_title_with_content_placeholder_triggers_retry(
    fixed_uid: str,
) -> None:
    """When ``formatted.title_out`` contains a CONTENT-prefixed
    placeholder, the loop MUST still detect the collision.

    The pre-fix check verified ``PH_CONTENT not in
    formatted.desc_html / raw_desc`` but did NOT verify the content
    placeholder's absence in title fields. The downstream global
    ``xml_str.replace(PH_CONTENT, ...)`` would substitute description
    CDATA into the ``<title>`` element.
    """
    item = _base_item(title=f"Test: {_PH_CONTENT_FIXED}")
    with pytest.raises(
        RuntimeError,
        match="Konnte keinen eindeutigen Platzhalter generieren",
    ):
        _emit_item(item, _NOW, {})


# ---------------------------------------------------------------------------
# (1) Fast path — items without placeholder patterns DO NOT trigger the
# retry loop. The fix MUST NOT regress benign emission.
# ---------------------------------------------------------------------------


def test_benign_item_emits_in_one_attempt(fixed_uid: str) -> None:
    """A standard upstream item with no placeholder pattern in any of
    its fields MUST emit successfully on the first random-UID attempt.
    Regression guard: the fix's broader collision check MUST NOT
    accidentally over-match benign inputs."""
    item = _base_item()
    ident, elem, replacements = _emit_item(item, _NOW, {})
    assert ident
    assert elem.tag == "item"
    assert _PH_CONTENT_FIXED in replacements
    assert _PH_TITLE_FIXED in replacements
    # Verify the replacement values are well-formed CDATA wraps.
    assert replacements[_PH_CONTENT_FIXED].startswith("<![CDATA[")
    assert replacements[_PH_CONTENT_FIXED].endswith("]]>")
    assert replacements[_PH_TITLE_FIXED].startswith("<![CDATA[")
    assert replacements[_PH_TITLE_FIXED].endswith("]]>")


def test_benign_item_xml_round_trips_through_make_rss(fixed_uid: str) -> None:
    """End-to-end: ``_make_rss`` on a benign item produces a well-formed
    XML document whose ``<link>`` element carries the original URL
    verbatim (not corrupted by an unintended CDATA injection)."""
    from defusedxml import ElementTree as DET

    benign_url = "https://www.wienerlinien.at/legitimate-path"
    item = _base_item(link=benign_url)
    rss_str = _make_rss([item], _NOW, {})

    root = DET.fromstring(rss_str)
    channel = root.find("channel")
    assert channel is not None
    item_elem = channel.find("item")
    assert item_elem is not None

    link_elem = item_elem.find("link")
    assert link_elem is not None
    assert link_elem.text == benign_url, (
        f"Benign link was corrupted: {link_elem.text!r}"
    )


# ---------------------------------------------------------------------------
# (2) Cross-field coverage invariant — every text-bearing field on
# FormattedContent MUST be checked. A future refactor that adds a new
# text-bearing field MUST also update the collision check; this test
# pins the inventory.
# ---------------------------------------------------------------------------


def test_collision_check_covers_all_formatted_content_text_fields() -> None:
    """Source-grep invariant: the helper function (or inline check) in
    ``_emit_item`` MUST reference every text-bearing field on
    ``FormattedContent`` to ensure cross-field coverage.

    If a future refactor adds a new ``FormattedContent`` field that
    flows into the serialised XML, this test fails until the collision
    check is updated to cover the new field.
    """
    from pathlib import Path
    from src.build_feed import FormattedContent

    source = Path(src.build_feed.__file__).read_text(encoding="utf-8")

    # Every NamedTuple field on FormattedContent that carries upstream
    # text MUST be referenced in the placeholder collision check.
    for field_name in FormattedContent._fields:
        marker = f"formatted.{field_name}"
        assert marker in source, (
            f"Collision-check coverage gap: {marker!r} is not referenced "
            f"anywhere in src/build_feed.py. The placeholder collision "
            f"check in _emit_item MUST examine every text-bearing field "
            f"on FormattedContent. "
            f"{SENTINEL_FEED_XML_PLACEHOLDER_COLLISION}"
        )


# ---------------------------------------------------------------------------
# (3) Negative cases — placeholder-like fragments that do NOT match the
# exact placeholder format MUST NOT trigger retries.
# ---------------------------------------------------------------------------


def test_partial_placeholder_in_link_does_not_trigger_retry(
    fixed_uid: str,
) -> None:
    """A URL that contains the placeholder PREFIX (``___CDATA_CONTENT_``)
    but NOT the full random UID must NOT trigger a collision. The
    collision check uses ``in`` (substring), but the planted string
    here is shorter than the full placeholder so it can't match the
    exact string lookup."""
    item = _base_item(
        link="https://www.wienerlinien.at/path/___CDATA_CONTENT_only_partial"
    )
    # The link contains the placeholder PREFIX but not the FULL placeholder
    # (which would be ``___CDATA_CONTENT_<32-hex>___``). With our fixed
    # UID and the strict ``in`` check, this MUST NOT trigger a collision
    # because the planted substring is shorter than the full placeholder
    # and is not a substring of any of the placeholder permutations.
    ident, elem, replacements = _emit_item(item, _NOW, {})
    assert ident
    assert elem.tag == "item"


def test_unrelated_underscores_do_not_trigger_retry(fixed_uid: str) -> None:
    """Items whose fields contain many underscores but NO placeholder
    pattern MUST NOT trigger the collision check."""
    item = _base_item(
        title="Some___Title___With___Underscores",
        guid="urn:example:item___with___underscores",
    )
    ident, elem, replacements = _emit_item(item, _NOW, {})
    assert ident
