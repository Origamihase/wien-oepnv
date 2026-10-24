"""The table of contents of ``docs/architecture.md`` matches its headings.

The document has over 3,000 lines; the list under "Inhalt" at its top is the
way in. Every ``##`` and ``###`` heading must be listed there in order, and
every link must point to the anchor GitHub and GitHub Pages (kramdown GFM)
derive from the heading text: lower case, every character that is not a
letter, digit, underscore, hyphen or space removed, spaces turned into
hyphens.
"""

from __future__ import annotations

import re
from pathlib import Path

_DOC = Path(__file__).resolve().parents[1] / "docs" / "architecture.md"
_TOC_LINK_RE = re.compile(r"^(  )?- \[(.+)\]\(#([^)]+)\)$")


def _slug(text: str) -> str:
    return re.sub(r"[^\w\- ]", "", text.lower()).replace(" ", "-")


def _split() -> tuple[list[str], str]:
    text = _DOC.read_text(encoding="utf-8")
    start = text.index("**Inhalt**\n\n")
    toc_block = text[start:].split("\n\n", 2)[1]
    return toc_block.splitlines(), text


def test_toc_lists_every_heading_in_order() -> None:
    toc_lines, text = _split()
    body = re.sub(r"```.*?```", "", text, flags=re.S)
    headings = re.findall(r"^(##|###) (.+)$", body, flags=re.M)
    expected = [("  " if level == "###" else "") + f"- [{title}](#{_slug(title)})" for level, title in headings]
    assert toc_lines == expected


def test_toc_links_are_well_formed() -> None:
    toc_lines, _ = _split()
    assert toc_lines
    for line in toc_lines:
        match = _TOC_LINK_RE.match(line)
        assert match, line
        assert match.group(3) == _slug(match.group(2))
