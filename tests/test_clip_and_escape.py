import importlib
import re
import sys
from pathlib import Path
from datetime import datetime


def _load_build_feed(monkeypatch):
    module_name = "src.build_feed"
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "src"))
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


def test_clip_text_html_plain_and_clips(monkeypatch):
    bf = _load_build_feed(monkeypatch)
    html_in = "<b>foo &amp; bar</b>"
    assert bf._clip_text_html(html_in, 100) == "foo & bar"
    assert bf._clip_text_html(html_in, 7) == "foo & …"


def test_clip_text_html_avoids_half_words(monkeypatch):
    bf = _load_build_feed(monkeypatch)
    assert bf._clip_text_html("foo bar baz", 8) == "foo bar …"
    assert bf._clip_text_html("Tom & Jerry", 5) == "Tom & …"
    assert bf._clip_text_html("Satz eins. Satz zwei.", 12) == "Satz eins. …"


def test_emit_item_sanitizes_description(monkeypatch):
    bf = _load_build_feed(monkeypatch)
    monkeypatch.setattr(bf, "DESCRIPTION_CHAR_LIMIT", 5)
    now = datetime(2024, 1, 1)
    ident, xml = bf._emit_item({"title": "X", "description": "<b>Tom & Jerry</b>"}, now, {})
    assert "<description><![CDATA[Tom & …]]></description>" in xml
    assert "Jerry" not in xml


def test_emit_item_keeps_bullet_separator(monkeypatch):
    bf = _load_build_feed(monkeypatch)
    now = datetime(2024, 1, 1)
    ident, xml = bf._emit_item({"title": "X", "description": "foo • bar"}, now, {})
    assert "foo • bar" in xml
    assert "foo\nbar" not in xml


def test_emit_item_collapses_whitespace(monkeypatch):
    bf = _load_build_feed(monkeypatch)
    now = datetime(2024, 1, 1)
    messy = {
        "title": "  Mehrfach\t  Leerzeichen   ",
        "description": "Zeile\t\tEins  mit   Tabs\nZeile Zwei\t  mit   Spaces",
    }
    ident, xml = bf._emit_item(messy, now, {})

    title_match = re.search(r"<title><!\[CDATA\[(.*)]]></title>", xml)
    assert title_match, xml
    assert "  " not in title_match.group(1)
    assert "\t" not in title_match.group(1)

    desc_match = re.search(r"<description><!\[CDATA\[(.*)]]></description>", xml)
    assert desc_match, xml
    desc_text = desc_match.group(1)
    assert "  " not in desc_text
    assert "\t" not in desc_text
