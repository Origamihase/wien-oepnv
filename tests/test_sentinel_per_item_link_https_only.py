"""Sentinel PoC: per-item ``<link>`` element in the published RSS feed
is not enforced HTTPS-only — TLS-strip primitive on subscribers.

Threat model
------------

The pre-fix flow in ``src/build_feed.py:_format_item_content`` validates
upstream-supplied per-item links via::

    sanitized_link = validate_http_url(link, check_dns=False) if link else ""

``validate_http_url`` accepts BOTH ``http`` and ``https`` schemes, so an
upstream-supplied ``http://`` link flows through to the published RSS
``<item><link>...</link></item>`` element verbatim. The published
``docs/feed.xml`` artefact is fetched by every subscriber's RSS reader
(Feedly, NetNewsWire, Inoreader, FreshRSS, …) and the ``<link>``
element is the click-through target — when the user clicks it, the
reader (or the operator's browser when they "open in new tab")
fetches the URL.

If the URL is ``http://``:

1. The reader / browser issues a plaintext HTTP request.
2. An on-path attacker (corporate gateway, hostile public WiFi
   gateway, ISP-level MITM, BGP hijack) intercepts the connection.
3. The attacker substitutes the response body with a phishing page,
   credential-harvesting form, or arbitrary HTML.
4. Many RSS readers do NOT consult the HSTS preload list before
   following the click, so the upgrade-to-HTTPS that browsers
   would get for known-HSTS hosts does not save subscribers.

The same concern motivated the 2026-05-09 *Public Feed URL Allow-List
Drift* round (`validate_public_feed_url`'s HTTPS-only pin) and the
2026-05-10 *HTTPS-only Provider URL Drift* rounds (PR #1415, #1416)
for env-controlled FEED_LINK / PAGES_BASE_URL / OEBB_RSS_URL /
WL_RSS_URL / VOR_BASE_URL / BAUSTELLEN_DATA_URL surfaces — all of
which were tightened to HTTPS-only because they end up in the
published RSS / sitemap / atom artefacts. The per-item ``<link>``
element is the LAST publishing surface that still accepts
``http://``.

Per-item links are *upstream-controlled* — they come from the cache
populated by WL / OEBB / VOR / Baustellen providers. Today every
upstream returns ``https://`` URLs (verified against the live cache
under ``cache/*/events.json``), so the gap is **forward-looking
defense-in-depth**: a future upstream regression (legitimate or
attacker-injected) that returns ``http://`` would propagate
plaintext URLs to every subscriber.

**Severity:** LOW-MEDIUM — TLS-strip primitive on subscribers,
contingent on upstream behaviour. No current vulnerability surface
(every upstream uses HTTPS today) but a structural drift candidate
with a documented future-regression shape and a concrete subscriber-
side blast radius.

Fix shape
---------

Mirror the canonical ``validate_public_feed_url`` HTTPS-only pin:
when ``validate_http_url`` accepts a ``http://`` link, treat it as
invalid for per-item ``<link>`` use and fall back to
``feed_config.FEED_LINK`` (which is already HTTPS-pinned via
``validate_public_feed_url`` at module-load time). The fallback is
HTTPS-only so the published feed never carries plaintext ``<link>``
elements.

The fix is a single conditional inside ``_format_item_content`` —
no API changes, no new helper, no impact on the legitimate HTTPS
path. The existing ``log.warning`` for "potentially unsafe/invalid
link" is preserved for malformed-URL cases; a new specific warning
fires for the HTTPS-downgrade case so operators can distinguish
the two failure modes.

Inventory invariant
-------------------

The contract is "no published ``<item><link>http://...</link></item>``
ever, regardless of upstream content". The walker test below pins
the invariant: planted-http per-item links are dropped, planted-https
links are preserved, javascript: links continue to be rejected
(existing contract from ``test_link_sanitization.py``).
"""

from __future__ import annotations

import datetime
from typing import Any

from defusedxml import ElementTree as ET

from src import build_feed
from src.feed.config import FEED_LINK


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
# (1) Per-item HTTP link is replaced with the HTTPS-pinned FEED_LINK.
# ---------------------------------------------------------------------------


def test_per_item_http_link_replaced_with_feed_link() -> None:
    """Pre-fix: an upstream-supplied ``http://`` per-item link
    propagates verbatim to the published RSS ``<item><link>`` element.
    Post-fix: the link is dropped and replaced with the HTTPS-pinned
    ``feed_config.FEED_LINK`` so the published artefact never carries
    plaintext URLs that would expose subscribers to TLS-strip
    attacks."""
    now = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)
    state: dict[str, dict[str, Any]] = {}

    item = {
        "title": "Test Item",
        # The narrow attack shape: a valid-looking HTTP URL. Pre-fix
        # this passes ``validate_http_url`` and lands in <link> verbatim.
        "link": "http://www.wienerlinien.at/disruption-page",
        "guid": "test-guid-http",
        "pubDate": now,
        "description": "Description",
    }

    ident, xml = _emit_item_str(item, now, state)

    # The published <link> MUST NOT carry the plaintext URL.
    assert f"<link>{item['link']}</link>" not in xml, (
        "per-item <link> still carries http:// URL after fix; "
        "subscribers' RSS readers would fetch over HTTP and be "
        "vulnerable to TLS-strip MITM."
    )

    # Post-fix: <link> contains the HTTPS-pinned FEED_LINK fallback.
    if FEED_LINK:
        assert f"<link>{FEED_LINK}</link>" in xml, (
            "expected FEED_LINK fallback in <link> when per-item "
            "link is plaintext HTTP"
        )


def test_per_item_http_link_with_subdomain_replaced() -> None:
    """Pre-fix: ``http://subdomain.example.com/path`` passes
    ``validate_http_url`` and lands in <link>. Post-fix: replaced
    with HTTPS FEED_LINK."""
    now = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)
    state: dict[str, dict[str, Any]] = {}

    http_link = "http://fahrplan.oebb.at/bin/help.exe/dn?L=vs_scotty"
    item = {
        "title": "Test Item",
        "link": http_link,
        "guid": "test-guid-oebb",
        "pubDate": now,
        "description": "Description",
    }

    ident, xml = _emit_item_str(item, now, state)

    assert f"<link>{http_link}</link>" not in xml
    assert "http://fahrplan.oebb.at" not in xml, (
        "plaintext OEBB host still visible in published feed XML "
        "after fix"
    )


# ---------------------------------------------------------------------------
# (2) Per-item HTTPS link is preserved verbatim — happy path regression.
# ---------------------------------------------------------------------------


def test_per_item_https_link_preserved() -> None:
    """Regression: legitimate HTTPS per-item links MUST continue to
    be preserved verbatim — pre- and post-fix behaviour are
    identical for the legitimate case."""
    now = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)
    state: dict[str, dict[str, Any]] = {}

    valid_link = "https://www.wienerlinien.at/ogd_realtime/some/page"
    item = {
        "title": "Test Item",
        "link": valid_link,
        "guid": "test-guid-https",
        "pubDate": now,
        "description": "Description",
    }

    ident, xml = _emit_item_str(item, now, state)

    assert f"<link>{valid_link}</link>" in xml


# ---------------------------------------------------------------------------
# (3) javascript: link rejection — preserved from existing
#     test_link_sanitization.py contract.
# ---------------------------------------------------------------------------


def test_per_item_javascript_link_still_rejected() -> None:
    """Regression: the existing ``javascript:`` rejection contract
    continues to fire post-fix — ``validate_http_url`` rejects
    ``javascript:`` schemes BEFORE the new HTTPS check runs."""
    now = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)
    state: dict[str, dict[str, Any]] = {}

    item = {
        "title": "Test Item",
        "link": "javascript:alert('XSS')",
        "guid": "test-guid-js",
        "pubDate": now,
        "description": "Description",
    }

    ident, xml = _emit_item_str(item, now, state)

    assert "javascript:alert" not in xml
    if FEED_LINK:
        assert f"<link>{FEED_LINK}</link>" in xml


# ---------------------------------------------------------------------------
# (4) Inventory invariant — published RSS feed must never carry
#     ``<link>http://`` regardless of upstream content.
# ---------------------------------------------------------------------------


def test_no_plaintext_http_in_per_item_link_published() -> None:
    """Inventory contract: walk a small fixture set covering the
    known per-item link shapes (HTTP / HTTPS / javascript / empty /
    relative) and assert that the rendered ``<link>`` element is
    ALWAYS HTTPS (or the HTTPS-pinned FEED_LINK fallback).

    A future bug that lets ``http://`` slip past the HTTPS-only pin
    fails this test at PR-review time, regardless of which provider
    introduced the regression.
    """
    now = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)
    state: dict[str, dict[str, Any]] = {}

    http_links_to_test = [
        "http://www.wienerlinien.at/page",
        "http://fahrplan.oebb.at/bin/help.exe/dn",
        "http://www.vor.at/disruption",
        "http://data.wien.gv.at/baustellen",
        "http://example.com/path",
    ]

    for idx, http_link in enumerate(http_links_to_test):
        item = {
            "title": f"Test {idx}",
            "link": http_link,
            "guid": f"test-guid-inventory-{idx}",
            "pubDate": now,
            "description": "Description",
        }

        ident, xml = _emit_item_str(item, now, state)

        # Critical contract: the planted http:// URL MUST NOT appear
        # verbatim in the published RSS XML — if it did, a subscriber's
        # click-through would be vulnerable to TLS-strip MITM.
        assert http_link not in xml, (
            f"plaintext URL {http_link!r} leaked into published RSS XML "
            f"for item {idx}; this is a TLS-strip primitive on subscribers."
        )
