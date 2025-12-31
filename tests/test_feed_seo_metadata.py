"""Tests ensuring feed output is SEO-friendly."""

from __future__ import annotations

import importlib
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


def _load_build_feed(monkeypatch):
    module_name = "src.build_feed"
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "src"))
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


def _emit_item_str(bf, item, now, state):
    ident, elem, replacements = bf._emit_item(item, now, state)
    xml_str = bf.ET.tostring(elem, encoding="unicode")
    for ph, content in replacements.items():
        xml_str = xml_str.replace(ph, content)
    return ident, xml_str


def test_emit_item_generates_stable_anchor_when_link_missing(monkeypatch):
    bf = _load_build_feed(monkeypatch)
    monkeypatch.setattr(bf.feed_config, "FEED_LINK", "https://example.com/wien-oepnv/")
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)

    ident, xml = _emit_item_str(bf, {"title": "Info", "description": "Hinweis"}, now, {})

    expected_link = bf._build_canonical_link(None, ident)
    link_match = re.search(r"<link>([^<]+)</link>", xml)
    assert link_match, xml
    assert link_match.group(1) == expected_link

    guid_match = re.search(r"<guid([^>]*)>([^<]+)</guid>", xml)
    assert guid_match, xml
    assert 'isPermaLink="false"' in guid_match.group(1)


def test_emit_item_keeps_permalink_guid_when_matching_link(monkeypatch):
    bf = _load_build_feed(monkeypatch)
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)

    item = {
        "title": "Störung",
        "description": "Details",
        "link": "https://verkehr.example/störung",
        "guid": "https://verkehr.example/störung",
    }

    _, xml = _emit_item_str(bf, item, now, {})

    guid_match = re.search(r"<guid([^>]*)>([^<]+)</guid>", xml)
    assert guid_match, xml
    assert guid_match.group(1) == ""
