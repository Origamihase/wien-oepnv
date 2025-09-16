import importlib
import re
import sys
from pathlib import Path
from datetime import datetime, timezone


def _load_build_feed(monkeypatch):
    module_name = "src.build_feed"
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "src"))
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


def _extract_description(xml: str) -> str:
    match = re.search(r"<description><!\[CDATA\[(.*)]]></description>", xml, re.S)
    assert match, xml
    return match.group(1)


def _extract_content_encoded(xml: str) -> str:
    match = re.search(r"<content:encoded><!\[CDATA\[(.*)]]></content:encoded>", xml, re.S)
    assert match, xml
    return match.group(1)


def _freeze_vienna_now(monkeypatch, bf, moment: datetime) -> None:
    real_datetime = bf.datetime

    class FrozenDateTime(real_datetime):  # type: ignore[misc]
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return moment.replace(tzinfo=None)
            return moment.astimezone(tz)

    monkeypatch.setattr(bf, "datetime", FrozenDateTime)


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
    assert title_match.group(1) == title_match.group(1).strip()

    desc_match = re.search(r"<description><!\[CDATA\[(.*)]]></description>", xml)
    assert desc_match, xml
    desc_text = desc_match.group(1)
    assert "  " not in desc_text
    assert "\t" not in desc_text
    assert desc_text == desc_text.strip()


def test_emit_item_trims_wrapping_whitespace(monkeypatch):
    bf = _load_build_feed(monkeypatch)
    now = datetime(2024, 1, 1)
    item = {
        "title": "\t  Foo  ",
        "description": " \t Foo  bar \n ",
    }

    _, xml = bf._emit_item(item, now, {})

    title_match = re.search(r"<title><!\[CDATA\[(.*)]]></title>", xml)
    assert title_match, xml
    assert title_match.group(1) == "Foo"

    desc_match = re.search(r"<description><!\[CDATA\[(.*)]]></description>", xml)
    assert desc_match, xml
    assert desc_match.group(1) == "Foo bar"


def test_emit_item_removes_category_and_limits_lines(monkeypatch):
    bf = _load_build_feed(monkeypatch)
    now = datetime(2024, 1, 1)
    item = {
        "title": "Bauarbeiten U6",
        "description": "Bauarbeiten\nWegen …\nZeitraum:\nMontag …",
    }

    _, xml = bf._emit_item(item, now, {})

    desc_text = _extract_description(xml)
    assert desc_text == "Wegen …"
    assert "Bauarbeiten" not in desc_text
    assert "Zeitraum" not in desc_text


def test_emit_item_appends_since_time(monkeypatch):
    bf = _load_build_feed(monkeypatch)
    _freeze_vienna_now(
        monkeypatch, bf, datetime(2024, 1, 10, tzinfo=bf._VIENNA_TZ)
    )
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    item = {
        "title": "Störung",
        "description": "Wegen Bauarbeiten",
        "starts_at": bf.datetime(2024, 1, 5, 10, 0, tzinfo=timezone.utc),
    }

    _, xml = bf._emit_item(item, now, {})

    desc_text = _extract_description(xml)
    assert desc_text == "Wegen Bauarbeiten<br/>Seit 05.01.2024"

    content_html = _extract_content_encoded(xml)
    assert content_html == "Wegen Bauarbeiten<br/>Seit 05.01.2024"


def test_emit_item_since_line_for_missing_or_nonadvancing_end(monkeypatch):
    bf = _load_build_feed(monkeypatch)
    _freeze_vienna_now(
        monkeypatch, bf, datetime(2024, 1, 10, tzinfo=bf._VIENNA_TZ)
    )
    now = datetime(2024, 1, 10, tzinfo=timezone.utc)
    base_item = {
        "title": "Störung",
        "description": "Wegen Bauarbeiten",
        "starts_at": bf.datetime(2024, 1, 5, 10, 0, tzinfo=timezone.utc),
    }

    scenarios = [
        {},
        {"ends_at": bf.datetime(2024, 1, 5, 10, 0, tzinfo=timezone.utc)},
    ]

    for extra in scenarios:
        item = dict(base_item)
        item.update(extra)
        _, xml = bf._emit_item(item, now, {})

        desc_text = _extract_description(xml)
        assert desc_text.split("<br/>") == [
            "Wegen Bauarbeiten",
            "Seit 05.01.2024",
        ]

        content_html = _extract_content_encoded(xml)
        assert content_html.split("<br/>") == [
            "Wegen Bauarbeiten",
            "Seit 05.01.2024",
        ]


def test_emit_item_future_same_day_shows_am(monkeypatch):
    bf = _load_build_feed(monkeypatch)
    _freeze_vienna_now(
        monkeypatch, bf, datetime(2024, 1, 1, tzinfo=bf._VIENNA_TZ)
    )
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    item = {
        "title": "Störung",
        "description": "Wegen Bauarbeiten",
        "starts_at": bf.datetime(2024, 1, 10, 8, 0, tzinfo=timezone.utc),
        "ends_at": bf.datetime(2024, 1, 10, 12, 0, tzinfo=timezone.utc),
    }

    _, xml = bf._emit_item(item, now, {})

    desc_text = _extract_description(xml)
    assert desc_text.split("<br/>") == [
        "Wegen Bauarbeiten",
        "Am 10.01.2024",
    ]

    content_html = _extract_content_encoded(xml)
    assert content_html.split("<br/>") == [
        "Wegen Bauarbeiten",
        "Am 10.01.2024",
    ]


def test_emit_item_future_start_without_end_shows_ab(monkeypatch):
    bf = _load_build_feed(monkeypatch)
    _freeze_vienna_now(
        monkeypatch, bf, datetime(2024, 1, 1, tzinfo=bf._VIENNA_TZ)
    )
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    item = {
        "title": "Info",
        "description": "Eingeschränkter Betrieb",
        "starts_at": bf.datetime(2024, 1, 20, 6, 0, tzinfo=timezone.utc),
    }

    _, xml = bf._emit_item(item, now, {})

    desc_text = _extract_description(xml)
    assert desc_text.split("<br/>") == [
        "Eingeschränkter Betrieb",
        "Ab 20.01.2024",
    ]

    content_html = _extract_content_encoded(xml)
    assert content_html.split("<br/>") == [
        "Eingeschränkter Betrieb",
        "Ab 20.01.2024",
    ]


def test_emit_item_appends_same_day_range(monkeypatch):
    bf = _load_build_feed(monkeypatch)
    _freeze_vienna_now(
        monkeypatch, bf, datetime(2024, 3, 11, tzinfo=bf._VIENNA_TZ)
    )
    now = datetime(2024, 3, 10, tzinfo=timezone.utc)
    item = {
        "title": "Sperre",
        "description": "Zug verkehrt nicht",
        "starts_at": bf.datetime(2024, 3, 10, 8, 0, tzinfo=timezone.utc),
        "ends_at": bf.datetime(2024, 3, 10, 12, 30, tzinfo=timezone.utc),
    }

    _, xml = bf._emit_item(item, now, {})

    desc_text = _extract_description(xml)
    assert desc_text == "Zug verkehrt nicht<br/>10.03.2024–10.03.2024"

    content_html = _extract_content_encoded(xml)
    assert content_html == "Zug verkehrt nicht<br/>10.03.2024–10.03.2024"


def test_emit_item_appends_multi_day_range(monkeypatch):
    bf = _load_build_feed(monkeypatch)
    now = datetime(2024, 6, 1, tzinfo=timezone.utc)
    item = {
        "title": "Sperre",
        "description": "Ersatzverkehr eingerichtet",
        "starts_at": datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc),
        "ends_at": datetime(2024, 6, 3, 15, 0, tzinfo=timezone.utc),
    }

    _, xml = bf._emit_item(item, now, {})

    desc_text = _extract_description(xml)
    assert desc_text == "Ersatzverkehr eingerichtet<br/>01.06.2024–03.06.2024"

    content_html = _extract_content_encoded(xml)
    assert content_html == "Ersatzverkehr eingerichtet<br/>01.06.2024–03.06.2024"


def test_emit_item_description_two_lines(monkeypatch):
    bf = _load_build_feed(monkeypatch)
    now = datetime(2024, 7, 1, tzinfo=timezone.utc)
    item = {
        "title": "Sperre",
        "description": "Ersatzverkehr eingerichtet",
        "starts_at": "2024-07-01T00:00:00+00:00",
        "ends_at": "2024-07-02T00:00:00+00:00",
    }

    _, xml = bf._emit_item(item, now, {})

    desc_text = _extract_description(xml)
    assert desc_text.split("<br/>") == [
        "Ersatzverkehr eingerichtet",
        "01.07.2024–02.07.2024",
    ]

    content_html = _extract_content_encoded(xml)
    assert content_html.split("<br/>") == [
        "Ersatzverkehr eingerichtet",
        "01.07.2024–02.07.2024",
    ]


def test_emit_item_no_times_only_description(monkeypatch):
    bf = _load_build_feed(monkeypatch)
    now = datetime(2024, 2, 1, tzinfo=timezone.utc)
    item = {
        "title": "Info",
        "description": "Kurzinfo",
    }

    _, xml = bf._emit_item(item, now, {})

    desc_text = _extract_description(xml)
    assert desc_text == "Kurzinfo"
