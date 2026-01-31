import os
import xml.etree.ElementTree as ET
from unittest.mock import patch
from pathlib import Path
import pytest
from scripts import generate_sitemap

# Mock data directory for tests
@pytest.fixture
def mock_docs_dir(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "index.md").touch()
    (docs_dir / "page.html").touch()
    (docs_dir / "feed.xml").touch()
    # Create a subdir to test recursion/exclusion
    (docs_dir / "subdir").mkdir()
    (docs_dir / "subdir" / "nested.md").touch()
    # Excluded dir
    (docs_dir / "_includes").mkdir()
    (docs_dir / "_includes" / "ignored.md").touch()

    # Patch the module's DOCS_DIR to point to our mock
    with patch("scripts.generate_sitemap.DOCS_DIR", docs_dir):
        yield docs_dir

def test_sitemap_xml_validity(mock_docs_dir):
    """Test that the generated sitemap is valid XML and contains expected URLs."""
    with patch.dict(os.environ, {"SITE_BASE_URL": "https://example.com/base"}):
        generate_sitemap.main()

    sitemap_path = mock_docs_dir / "sitemap.xml"
    assert sitemap_path.exists()

    tree = ET.parse(sitemap_path)
    root = tree.getroot()

    assert root.tag == "{http://www.sitemaps.org/schemas/sitemap/0.9}urlset"
    urls = [elem.text for elem in root.findall(".//{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]

    assert "https://example.com/base/" in urls  # index.md -> /
    assert "https://example.com/base/page.html" in urls
    assert "https://example.com/base/feed.xml" in urls
    assert "https://example.com/base/subdir/nested.html" in urls

    # Ensure excluded files are not present
    assert not any("_includes" in url for url in urls)

def test_sitemap_escaping(mock_docs_dir):
    """Test that special characters in base URL are escaped."""
    # This URL triggers invalid XML if not escaped
    risky_url = "https://example.com/foo&bar"

    with patch.dict(os.environ, {"SITE_BASE_URL": risky_url}):
        generate_sitemap.main()

    sitemap_path = mock_docs_dir / "sitemap.xml"
    content = sitemap_path.read_text(encoding="utf-8")

    # Check that it is valid XML (parser would fail otherwise)
    ET.parse(sitemap_path)

    # Check raw content for escaping
    assert "foo&amp;bar" in content
    assert "foo&bar" not in content  # Should not be present unescaped
