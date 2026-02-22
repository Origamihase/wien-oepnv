import pytest
from datetime import datetime, timezone
import xml.etree.ElementTree as ET

from src.build_feed import _emit_item, feed_config

def test_emit_item_formatting_html_stripping():
    # Setup
    now = datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    state = {}

    # Input with HTML and potential artifacts
    # "h2Kranarbeiten/h2" artifact comes from bad regex stripping.
    # Here we test that our new logic handles <h2> correctly.
    item = {
        "title": "Title",
        "description": "<h2>Kranarbeiten</h2><p>Details here.</p>",
        "guid": "guid1",
        "starts_at": datetime(2023, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
        "ends_at": datetime(2023, 1, 1, 14, 0, 0, tzinfo=timezone.utc),
    }

    # Execute
    ident, elem, replacements = _emit_item(item, now, state)

    # Verify Description Placeholder Content
    desc_elem = elem.find("description")
    assert desc_elem is not None
    placeholder = desc_elem.text
    assert placeholder in replacements

    content = replacements[placeholder]
    inner_content = content.replace("<![CDATA[", "").replace("]]>", "")

    # Check for strict 2-line layout
    # Expected: "Kranarbeiten Details here." (from html_to_text with collapse_newlines=True)
    # <br/>
    # [Am 01.01.2023] (or similar depending on time)

    # Ensure no HTML tags
    assert "<h2>" not in inner_content
    assert "</p>" not in inner_content

    # Ensure no artifacts
    assert "h2Kranarbeiten" not in inner_content
    assert "/h2" not in inner_content

    # Ensure content is there
    assert "Kranarbeiten" in inner_content
    assert "Details here" in inner_content

    # Ensure 2-line layout
    assert "<br/>" in inner_content
    assert "[" in inner_content
    assert "]" in inner_content

def test_emit_item_formatting_plain_text():
    # Setup
    now = datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    state = {}

    item = {
        "title": "Title",
        "description": "Just plain text.",
        "guid": "guid2",
        "starts_at": None,
        "ends_at": None,
    }

    # Execute
    ident, elem, replacements = _emit_item(item, now, state)

    desc_elem = elem.find("description")
    placeholder = desc_elem.text
    content = replacements[placeholder]
    inner_content = content.replace("<![CDATA[", "").replace("]]>", "")

    assert "Just plain text." in inner_content
    # No time line -> No <br/>
    assert "<br/>" not in inner_content

def test_emit_item_formatting_multiline_collapsed():
    # Setup
    now = datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    state = {}

    item = {
        "title": "Title",
        "description": "Line 1.\nLine 2.",
        "guid": "guid3",
        "starts_at": None,
        "ends_at": None,
    }

    # Execute
    ident, elem, replacements = _emit_item(item, now, state)

    desc_elem = elem.find("description")
    placeholder = desc_elem.text
    content = replacements[placeholder]
    inner_content = content.replace("<![CDATA[", "").replace("]]>", "")

    # Should be collapsed to space or bullet
    # html_to_text(collapse_newlines=True) replaces newlines with " • " or space?
    # src/utils/text.py: newline_replacement = " • " if collapse_newlines else "\n"

    assert "Line 1. • Line 2." in inner_content or "Line 1. Line 2." in inner_content

def test_emit_item_timeframe_formatting():
    # Setup
    now = datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    state = {}

    # Start and End known
    item = {
        "title": "Title",
        "description": "Desc",
        "guid": "guid4",
        "starts_at": datetime(2023, 1, 1, 8, 0, 0, tzinfo=timezone.utc),
        "ends_at": datetime(2023, 1, 2, 8, 0, 0, tzinfo=timezone.utc),
    }

    ident, elem, replacements = _emit_item(item, now, state)
    desc_elem = elem.find("description")
    placeholder = desc_elem.text
    content = replacements[placeholder]
    inner_content = content.replace("<![CDATA[", "").replace("]]>", "")

    # Check date formatting
    # Should contain dates
    assert "01.01.2023" in inner_content
    assert "[" in inner_content
    assert "]" in inner_content
