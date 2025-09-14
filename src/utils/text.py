#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Text utilities."""

import html
import re
from html.parser import HTMLParser


_WS_RE = re.compile(r"[ \t\r\f\v]+")

# Common German prepositions that should not be followed by a bullet.
PREPOSITIONS = {"bei", "in", "an", "auf"}

_PREP_BULLET_RE = re.compile(
    rf"\b({'|'.join(map(re.escape, PREPOSITIONS))})\s*•\s*", re.IGNORECASE
)


def normalize_bullets(text: str) -> str:
    """Remove bullets that directly follow known prepositions."""
    return _PREP_BULLET_RE.sub(r"\1 ", text)


class _HTMLToTextParser(HTMLParser):
    """Lightweight HTML-to-text parser that inserts newlines and bullets."""

    _BLOCK_TAGS = {
        "p",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "table",
        "tr",
        "td",
        "th",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: D401, ANN001
        tag = tag.lower()
        if tag == "br":
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n• ")
        elif tag in self._BLOCK_TAGS:
            self.parts.append("\n")
        # ul/ol are structural containers; no separator on start

    def handle_endtag(self, tag: str) -> None:  # noqa: D401, ANN001
        tag = tag.lower()
        if tag == "li":
            self.parts.append("\n")
        elif tag in self._BLOCK_TAGS or tag in {"ul", "ol"}:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs) -> None:  # noqa: D401, ANN001
        tag = tag.lower()
        if tag == "br":
            self.parts.append("\n")
        elif tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:  # noqa: D401
        self.parts.append(data)


def html_to_text(s: str) -> str:
    """Convert HTML fragments to plain text with a uniform bullet separator."""
    if not s:
        return ""

    parser = _HTMLToTextParser()
    parser.feed(s)
    parser.close()

    txt = "".join(parser.parts)
    txt = html.unescape(txt)
    txt = re.sub(r"\s*\n\s*", " • ", txt)
    txt = re.sub(r"(\d)([a-zäöüß])", r"\1 \2", txt)
    txt = _WS_RE.sub(" ", txt)
    # Collapse any repeated bullet separators before removing those after prepositions
    txt = re.sub(r"(?:\s*•\s*){2,}", " • ", txt)
    txt = normalize_bullets(txt)
    txt = txt.strip()
    txt = re.sub(r"^•\s*", "", txt)
    txt = re.sub(r"\s*•$", "", txt)
    txt = _WS_RE.sub(" ", txt)
    txt = re.sub(r"\s{2,}", " ", txt).strip()
    return txt

