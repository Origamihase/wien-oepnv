#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Text utilities."""

import html
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

# Void elements (self-closing) shouldn't be added to closing stack
# Source: https://html.spec.whatwg.org/multipage/syntax.html#void-elements
VOID_ELEMENTS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


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

    # Hard cap at 5000 characters to prevent ReDoS
    if len(txt) > 5000:
        txt = txt[:5000] + "... [TRUNCATED]"
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


def escape_markdown(text: str) -> str:
    """Escape HTML and Markdown characters to prevent injection/XSS."""
    text = html.escape(text)
    # Escape Markdown characters that could create links or formatting
    # We backslash-escape: [ ] ( ) * _ `
    for char in "[]()*_`":
        text = text.replace(char, f"\\{char}")
    return text


def escape_markdown_cell(text: str) -> str:
    """Escape pipe characters and HTML to prevent injection and table breakage."""
    escaped = escape_markdown(text)
    # Use HTML entity for pipe to be safe in tables
    return escaped.replace("|", "&#124;")

class HTMLTruncator(HTMLParser):
    """Truncates HTML content while preserving tags and structure."""

    def __init__(self, limit: int, ellipsis: str = "...") -> None:
        super().__init__(convert_charrefs=False)
        self.limit = limit
        self.ellipsis = ellipsis
        self.current_length = 0
        self.output: list[str] = []
        self.tags_stack: list[str] = []
        self.done = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.done:
            return

        # Reconstruct the tag
        attr_str = ""
        for attr, value in attrs:
            if value is None:
                attr_str += f" {attr}"
            else:
                # Basic escaping for attribute values
                val_escaped = html.escape(value, quote=True)
                attr_str += f' {attr}="{val_escaped}"'

        self.output.append(f"<{tag}{attr_str}>")

        if tag.lower() not in VOID_ELEMENTS:
            self.tags_stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if self.done:
            return

        self.output.append(f"</{tag}>")
        # Try to match with the most recent open tag
        if self.tags_stack:
            if self.tags_stack[-1] == tag:
                self.tags_stack.pop()
            else:
                # Tag mismatch (malformed HTML?), try to find it up the stack
                if tag in self.tags_stack:
                    while self.tags_stack and self.tags_stack[-1] != tag:
                        self.tags_stack.pop()
                    self.tags_stack.pop() # Pop the matching tag

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.done:
            return
        # Self-closing tag like <br />
        attr_str = ""
        for attr, value in attrs:
            if value is None:
                attr_str += f" {attr}"
            else:
                val_escaped = html.escape(value, quote=True)
                attr_str += f' {attr}="{val_escaped}"'
        self.output.append(f"<{tag}{attr_str} />")

    def handle_data(self, data: str) -> None:
        if self.done:
            return

        remaining = self.limit - self.current_length
        if len(data) > remaining:
            candidate = data[:remaining]
            last_space = candidate.rfind(" ")
            if last_space != -1:
                cut_index = last_space
            else:
                cut_index = remaining

            self.output.append(data[:cut_index])
            self.output.append(self.ellipsis)
            self.current_length += cut_index
            self.done = True
        else:
            self.output.append(data)
            self.current_length += len(data)

    def handle_entityref(self, name: str) -> None:
        if self.done:
            return
        # Count entity as 1 character for display length
        entity = f"&{name};"
        if self.limit - self.current_length >= 1:
            self.output.append(entity)
            self.current_length += 1
        else:
            self.output.append(self.ellipsis)
            self.done = True

    def handle_charref(self, name: str) -> None:
        if self.done:
            return
        entity = f"&#{name};"
        if self.limit - self.current_length >= 1:
            self.output.append(entity)
            self.current_length += 1
        else:
            self.output.append(self.ellipsis)
            self.done = True

    def close_open_tags(self) -> None:
        # Close remaining tags in reverse order
        while self.tags_stack:
            tag = self.tags_stack.pop()
            self.output.append(f"</{tag}>")


def truncate_html(text: str, limit: int, ellipsis: str = "...") -> str:
    """
    Truncate HTML text to a specified character limit (of content), preserving tags.
    Ensures all opened tags are closed.
    """
    if not text:
        return ""

    # Quick check if it's even needed
    # Note: This is a loose check because tags add length but don't count towards content limit.
    # But if raw length is <= limit, we certainly don't need to truncate content.
    if len(text) <= limit:
        return text

    parser = HTMLTruncator(limit, ellipsis)
    parser.feed(text)
    parser.close_open_tags()

    return "".join(parser.output)
