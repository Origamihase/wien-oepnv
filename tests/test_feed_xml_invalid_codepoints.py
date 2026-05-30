"""Regression test: XML-invalid code points in a feed item must not break the
rendered feed (``src/build_feed.py`` — ``_CONTROL_RE`` / ``_identity_for_item``).

ElementTree serialises item title/description/guid into the public
``docs/feed.xml`` + ``feed.en.xml``. Two byte shapes from a hostile / garbled
upstream value used to survive ``_sanitize_text`` and break the feed:

* **U+FFFE / U+FFFF** — forbidden by the XML 1.0 ``Char`` production, so a
  planted title produced a feed that failed to parse
  (``ParseError: not well-formed``) in every subscriber's reader.
* **U+D800-U+DFFF surrogates** — a lone surrogate (reachable via a ``\\uD800``
  escape in an upstream JSON title) raised ``UnicodeEncodeError`` at the
  ``_identity_for_item`` hash encode (which runs on the *raw* title before
  ``_sanitize_text`` cleans the rendered one), aborting the whole build.

The fix adds the surrogate range + ``U+FFFE``/``U+FFFF`` to ``_CONTROL_RE`` and
encodes the identity hashes with ``errors="surrogatepass"``. Crucially, the
Unicode noncharacters U+FDD0-U+FDEF and supplementary-plane U+nFFFE/U+nFFFF are
*valid* per the XML 1.0 grammar and MUST be preserved (stripping them would be
silent data loss) — this test pins both the strip set and the keep set.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from typing import cast

from src.build_feed import _identity_for_item, _make_rss, _sanitize_text
from src.feed_types import FeedItem

_NOW = datetime(2026, 5, 30, tzinfo=UTC)


def _render(title: str) -> str:
    item = cast(
        FeedItem,
        {
            "title": title,
            "description": "",
            "guid": "g",
            "link": "https://example.com",
            "source": "WL",
            "category": "t",
        },
    )
    return _make_rss([item], _NOW, {}, lang="de")


# ---- strip set: the codepoints that actually break the feed --------------


def test_fffe_ffff_yield_well_formed_xml() -> None:
    """U+FFFE / U+FFFF must be stripped so the feed parses."""
    for ch, label in (("￾", "U+FFFE"), ("￿", "U+FFFF")):
        out = _render(f"Stoerung {ch} U6")
        ET.fromstring(out)  # must not raise ParseError
        assert ch not in out, f"{label} survived into the feed"


def test_lone_surrogate_does_not_crash_the_build() -> None:
    """A lone surrogate must neither crash the identity hash nor the render."""
    out = _render("Stoerung \ud800 U6")  # would raise UnicodeEncodeError pre-fix
    ET.fromstring(out)
    assert "\ud800" not in out


def test_identity_for_item_survives_surrogate() -> None:
    """``_identity_for_item`` must hash a surrogate-bearing title, not crash."""
    ident = _identity_for_item(
        cast(
            FeedItem,
            {
                "title": "X\ud800Y",
                "source": "wl",
                "category": "stoerung",
                "guid": "g",
                "link": "https://example.com",
                "description": "",
            },
        )
    )
    assert isinstance(ident, str) and ident


def test_sanitize_text_strips_invalid_keeps_valid() -> None:
    assert _sanitize_text("a￾b￿c\ud800d") == "abcd"


# ---- keep set: valid-per-XML code points must be preserved ---------------


def test_valid_noncharacters_and_unicode_preserved() -> None:
    """U+FDD0-FDEF, supplementary noncharacters, umlauts and emoji are valid."""
    for ch in ("﷐", "﷯", "\U0001fffe", "ä", "ß", "🚇"):
        out = _render(f"Stoerung {ch} U6")
        ET.fromstring(out)
        assert ch in out, f"valid code point {ch!r} was wrongly stripped"
