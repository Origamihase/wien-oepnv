from pathlib import Path
from src.seo.atom_links import apply_atom_links
from src.seo.robots import format_robots
from src.seo.sitemap import rewrite_canonicals, apply_to_path
import hashlib

def test_atom_links_idempotent() -> None:
    site_base = "https://origamihase.github.io/wien-oepnv/"
    feed_xml = """<?xml version='1.0' encoding='utf-8'?>
<rss xmlns:atom="http://www.w3.org/2005/Atom" version="2.0">
    <channel>
        <title>Test Feed</title>
        <description>Description</description>
    </channel>
</rss>
"""
    first_pass = apply_atom_links(feed_xml, site_base)
    second_pass = apply_atom_links(first_pass, site_base)

    assert hashlib.sha256(first_pass.encode()).hexdigest() == hashlib.sha256(second_pass.encode()).hexdigest()
    assert first_pass == second_pass

def test_atom_links_inject_into_minimal_feed() -> None:
    site_base = "https://example.com"
    feed_xml = """<?xml version='1.0' encoding='utf-8'?>
<rss version="2.0">
    <channel>
        <title>Test Feed</title>
        <description>Minimal</description>
    </channel>
</rss>
"""
    result = apply_atom_links(feed_xml, site_base)
    assert 'xmlns:atom="http://www.w3.org/2005/Atom"' in result
    assert '<atom:link rel="alternate" type="text/html" href="https://example.com/"' in result
    assert '<atom:link rel="self" type="application/rss+xml" href="https://example.com/feed.xml"' in result
    assert '<language>de</language>' in result

def test_atom_links_replace_existing() -> None:
    site_base = "https://new.com"
    feed_xml = """<?xml version='1.0' encoding='utf-8'?>
<rss xmlns:atom="http://www.w3.org/2005/Atom" version="2.0">
    <channel>
        <title>Test Feed</title>
        <description>Minimal</description>
        <atom:link rel="self" href="http://old.com/"/>
        <language>en</language>
    </channel>
</rss>
"""
    result = apply_atom_links(feed_xml, site_base)
    assert "http://old.com" not in result
    assert "<language>en</language>" not in result
    assert '<language>de</language>' in result
    assert 'href="https://new.com/feed.xml"' in result

def test_atom_links_preserve_unrelated() -> None:
    site_base = "https://test.com"
    feed_xml = """<?xml version='1.0' encoding='utf-8'?>
<rss xmlns:atom="http://www.w3.org/2005/Atom" version="2.0">
    <channel>
        <title>Test Feed</title>
        <description>Minimal</description>
        <lastBuildDate>Wed, 02 Oct 2002 13:00:00 GMT</lastBuildDate>
        <item>
            <title>Item 1</title>
        </item>
    </channel>
</rss>
"""
    result = apply_atom_links(feed_xml, site_base)
    assert "<lastBuildDate>Wed, 02 Oct 2002 13:00:00 GMT</lastBuildDate>" in result
    assert "<title>Item 1</title>" in result

def test_robots_canonical_sitemap() -> None:
    content = """User-agent: *
Allow: /
Sitemap: http://old.com/sitemap.xml
Sitemap: http://another.com/sitemap.xml
"""
    result = format_robots(content, "https://canonical.com/")
    assert "User-agent: *" in result
    assert "Allow: /" in result
    assert "Sitemap: http://old.com/sitemap.xml" not in result
    assert "Sitemap: http://another.com/sitemap.xml" not in result
    assert "Sitemap: https://canonical.com/sitemap.xml" in result

def test_robots_strip_leading_whitespace() -> None:
    content = """    User-agent: *
  Allow: /
"""
    result = format_robots(content, "https://canonical.com")
    assert "User-agent: *" in result
    assert "Allow: /" in result
    assert "    User-agent: *" not in result
    assert "  Allow: /" not in result

def test_sitemap_no_op_when_missing(tmp_path: Path) -> None:
    missing_file = tmp_path / "does_not_exist.xml"
    assert apply_to_path(missing_file, "https://example.com") is False

def test_sitemap_canonical_replace_all_eight_patterns() -> None:
    base = "https://origamihase.github.io/wien-oepnv"
    sitemap = """
<loc>https://wien-oepnv.github.io/</loc>
<loc>https://wien-oepnv.github.io</loc>
<loc>https://origamihase.github.io/wien-oepnv/</loc>
<loc>https://origamihase.github.io/wien-oepnv</loc>
<loc>https://wien-oepnv.github.io/feed.xml</loc>
<loc>https://origamihase.github.io/wien-oepnv/feed.xml</loc>
<loc>https://wien-oepnv.github.io/docs/how-to/</loc>
<loc>https://wien-oepnv.github.io/docs/how-to</loc>
<loc>https://origamihase.github.io/wien-oepnv/docs/how-to/</loc>
<loc>https://origamihase.github.io/wien-oepnv/docs/how-to</loc>
<loc>https://wien-oepnv.github.io/docs/reference/</loc>
<loc>https://wien-oepnv.github.io/docs/reference</loc>
<loc>https://origamihase.github.io/wien-oepnv/docs/reference/</loc>
<loc>https://origamihase.github.io/wien-oepnv/docs/reference</loc>
"""
    result = rewrite_canonicals(sitemap, base)

    assert f"<loc>{base}/</loc>" in result
    assert f"<loc>{base}/feed.xml</loc>" in result
    assert f"<loc>{base}/docs/how-to/</loc>" in result
    assert f"<loc>{base}/docs/reference/</loc>" in result

    # Assert old ones are gone
    assert "wien-oepnv.github.io" not in result
    # We shouldn't have any links that are missing the trailing slash
    # unless it's feed.xml
    assert f"<loc>{base}</loc>" not in result
    assert f"<loc>{base}/docs/how-to</loc>" not in result
    assert f"<loc>{base}/docs/reference</loc>" not in result
