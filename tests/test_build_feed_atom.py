"""Tests for atom:link / language SEO metadata in the generated RSS.

These tests cover the migration of SEO normalization from the Perl-based
"Normalize feed metadata (SEO)" step in .github/workflows/build-feed.yml
into the Python feed builder (_make_rss in src/build_feed.py). They guard
against regressions in atom namespace declaration, atom:link self/alternate
emission, and the <language>de</language> tag.
"""
from __future__ import annotations

from datetime import datetime, timezone

from defusedxml import ElementTree as ET
import pytest

from src.build_feed import _make_rss


_NOW = datetime(2026, 5, 1, 18, 0, 0, tzinfo=timezone.utc)
_ATOM_NS = "http://www.w3.org/2005/Atom"


@pytest.fixture
def pages_base_url(monkeypatch):
    """Set PAGES_BASE_URL via env and refresh feed config; restore on teardown."""

    import src.build_feed

    def _set(url: str) -> None:
        monkeypatch.setenv("PAGES_BASE_URL", url)
        src.build_feed.feed_config.refresh_from_env()

    yield _set
    monkeypatch.delenv("PAGES_BASE_URL", raising=False)
    src.build_feed.feed_config.refresh_from_env()


def test_make_rss_declares_atom_namespace(pages_base_url):
    pages_base_url("https://example.github.io/test-repo")
    rss_str = _make_rss([], _NOW, {})
    assert 'xmlns:atom="http://www.w3.org/2005/Atom"' in rss_str


def test_make_rss_emits_self_and_alternate_atom_links(pages_base_url):
    pages_base_url("https://example.github.io/test-repo")
    rss_str = _make_rss([], _NOW, {})

    root = ET.fromstring(rss_str)
    channel = root.find("channel")
    assert channel is not None

    atom_links = channel.findall(f"{{{_ATOM_NS}}}link")
    assert len(atom_links) == 2, "expected one self plus one alternate atom:link"

    by_rel = {link.get("rel"): link for link in atom_links}
    assert set(by_rel.keys()) == {"self", "alternate"}

    alt = by_rel["alternate"]
    assert alt.get("type") == "text/html"
    assert alt.get("href") == "https://example.github.io/test-repo/"

    self_link = by_rel["self"]
    assert self_link.get("type") == "application/rss+xml"
    assert self_link.get("href") == "https://example.github.io/test-repo/feed.xml"


def test_make_rss_emits_language_de(pages_base_url):
    pages_base_url("https://example.github.io/test-repo")
    rss_str = _make_rss([], _NOW, {})

    root = ET.fromstring(rss_str)
    channel = root.find("channel")
    assert channel is not None

    language = channel.find("language")
    assert language is not None
    assert language.text == "de"


def test_make_rss_strips_trailing_slash_from_pages_base_url(pages_base_url):
    """A trailing slash in PAGES_BASE_URL must not produce a double slash in href."""

    pages_base_url("https://example.github.io/test-repo/")
    rss_str = _make_rss([], _NOW, {})

    root = ET.fromstring(rss_str)
    channel = root.find("channel")
    assert channel is not None
    atom_links = channel.findall(f"{{{_ATOM_NS}}}link")

    for link in atom_links:
        href = link.get("href") or ""
        assert "//feed.xml" not in href
        assert not href.endswith("//"), f"unexpected double slash in {href!r}"
