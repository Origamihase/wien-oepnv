#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Text utilities."""

import re
from html.parser import HTMLParser
from typing import Match


_WS_RE = re.compile(r"[ \t\r\f\v]+")

# Common German prepositions that should not be followed by a bullet.
PREPOSITIONS: tuple[str, ...] = (
    # Alphabetical order for easier maintenance; keep umlaut/ASCII pairs
    # together for readability.
    "ab",
    "am",
    "an",
    "auf",
    "bei",
    "bis",
    "durch",
    "gegen",
    "in",
    "nach",
    "ueber",
    "über",
    "vom",
    "zum",
    "zur",
)

_PREP_BULLET_RE = re.compile(
    rf"\b({'|'.join(re.escape(p) for p in PREPOSITIONS)})\s*•\s*",
    re.IGNORECASE,
)

# Precompiled regexes for html_to_text cleanup
_NEWLINE_CLEANUP_RE = re.compile(r"[ \t\r\f\v]*\n[ \t\r\f\v]*")
_COLON_BULLET_RE = re.compile(r":\s*•\s*")
_COLON_NEWLINE_RE = re.compile(r":\s*\n")
_DIGIT_ALPHA_RE = re.compile(r"(\d)([A-Za-zÄÖÜäöüß])")
_MULTI_BULLET_RE = re.compile(r"(?:\s*•\s*){2,}")
_LEADING_BULLET_RE = re.compile(r"^\s*•\s*")
_TRAILING_BULLET_RE = re.compile(r"\s*•\s*$")
_MULTI_SPACE_RE = re.compile(r"\s{2,}")


def normalize_bullets(text: str) -> str:
    """Remove bullets that directly follow known prepositions."""

    def _repl(match: Match[str]) -> str:
        prefix = match.group(1)
        tail = match.group(0)[len(prefix):]
        if "\n" in tail:
            return prefix + "\n"
        return prefix + " "

    return _PREP_BULLET_RE.sub(_repl, text)


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
    _IGNORE_TAGS = {"script", "style"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignore_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: D401, ANN001
        tag = tag.lower()
        if tag in self._IGNORE_TAGS:
            self._ignore_depth += 1
            return
        if self._ignore_depth > 0:
            return

        if tag == "br":
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n• ")
        elif tag in self._BLOCK_TAGS:
            self.parts.append("\n")
        # ul/ol are structural containers; no separator on start

    def handle_endtag(self, tag: str) -> None:  # noqa: D401, ANN001
        tag = tag.lower()
        if tag in self._IGNORE_TAGS:
            if self._ignore_depth > 0:
                self._ignore_depth -= 1
            return
        if self._ignore_depth > 0:
            return

        if tag == "li":
            self.parts.append("\n")
        elif tag in self._BLOCK_TAGS or tag in {"ul", "ol"}:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs) -> None:  # noqa: D401, ANN001
        tag = tag.lower()
        if self._ignore_depth > 0:
            return

        if tag == "br":
            self.parts.append("\n")
        elif tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:  # noqa: D401
        if self._ignore_depth > 0:
            return
        self.parts.append(data)


def html_to_text(s: str, *, collapse_newlines: bool = False) -> str:
    """Convert HTML fragments to plain text.

    ``collapse_newlines`` can be set to ``True`` to restore the legacy behaviour
    where line breaks were replaced by the ``" • "`` separator.
    """
    if not s:
        return ""

    parser = _HTMLToTextParser()
    parser.feed(s)
    parser.close()

    txt = "".join(parser.parts)
    # Note: html.unescape is skipped because HTMLParser(convert_charrefs=True)
    # already decodes entities. Calling unescape again is redundant.
    txt = txt.replace("\xa0", " ")

    newline_replacement = " • " if collapse_newlines else "\n"
    txt = _NEWLINE_CLEANUP_RE.sub(newline_replacement, txt)
    if collapse_newlines:
        txt = _COLON_BULLET_RE.sub(": ", txt)
    else:
        txt = _COLON_NEWLINE_RE.sub(":\n", txt)
    txt = _DIGIT_ALPHA_RE.sub(r"\1 \2", txt)
    txt = _WS_RE.sub(" ", txt)
    # Collapse any repeated bullet separators before removing those after prepositions
    txt = _MULTI_BULLET_RE.sub(" • ", txt)
    txt = normalize_bullets(txt)

    strip_border_bullets = collapse_newlines or " • " in txt
    if strip_border_bullets:
        txt = _LEADING_BULLET_RE.sub("", txt)
        txt = _TRAILING_BULLET_RE.sub("", txt)

    if collapse_newlines:
        txt = _WS_RE.sub(" ", txt)
        txt = _MULTI_SPACE_RE.sub(" ", txt).strip()
    else:
        # Optimization: _NEWLINE_CLEANUP_RE and _MULTI_NEWLINE_RE are
        # effectively handled by split/strip/join below.
        lines = [line.strip() for line in txt.split("\n")]
        txt = "\n".join(line for line in lines if line)

    return txt
