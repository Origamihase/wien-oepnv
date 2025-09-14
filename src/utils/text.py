#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Text utilities."""

import html
import re

# Precompiled regular expressions for HTML-to-text conversion
_BR_RE = re.compile(r"(?i)<\s*br\s*/?\s*>")
_BLOCK_CLOSE_RE = re.compile(r"(?is)</\s*(p|div|li|ul|ol|h\d|table|tr|td|th)\s*>")
_BLOCK_OPEN_RE = re.compile(r"(?is)<\s*(p|div|ul|ol|h\d|table|tr|td|th)\b[^>]*>")
_LI_OPEN_RE = re.compile(r"(?is)<\s*li\b[^>]*>")
_TAG_RE = re.compile(r"(?is)<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_PREP_BULLET_RE = re.compile(r"\b(bei|in|an|auf)\s*•\s*", re.IGNORECASE)


def html_to_text(s: str) -> str:
    """Convert HTML fragments to plain text with a uniform bullet separator."""
    if not s:
        return ""
    txt = html.unescape(s)
    txt = _BR_RE.sub("\n", txt)
    txt = _BLOCK_CLOSE_RE.sub("\n", txt)
    txt = _LI_OPEN_RE.sub("• ", txt)
    txt = _BLOCK_OPEN_RE.sub("", txt)
    txt = _TAG_RE.sub("", txt)
    txt = re.sub(r"(?<=\S)•", " •", txt)
    lines = [line.strip() for line in txt.split("\n") if line.strip()]
    if lines:
        txt = lines[0]
        for line in lines[1:]:
            if line.startswith("•"):
                txt += " " + line
            else:
                txt += " • " + line
    else:
        txt = ""
    txt = txt.lstrip("• ")
    txt = re.sub(r"(\d)([A-Za-zÄÖÜäöüß])", r"\1 \2", txt)
    txt = _WS_RE.sub(" ", txt)
    txt = re.sub(r"\s{2,}", " ", txt).strip()
    txt = _PREP_BULLET_RE.sub(r"\1 ", txt)
    return txt
