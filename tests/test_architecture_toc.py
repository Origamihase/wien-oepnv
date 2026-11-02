"""The table of contents of ``docs/architecture.md`` matches its headings.

The document has over 3,000 lines in seven parts (``architecture.md`` and
``architecture-teil-2.md`` …, 700-line limit since 2026-11-02); the list
under "Inhalt" at the top of the first part is the way in. Every ``##`` and
``###`` heading of every part must be listed there in order, and every link
must point to the part that holds the heading and to the anchor GitHub and
GitHub Pages (kramdown GFM) derive from the heading text: lower case, every
character that is not a letter, digit, underscore, hyphen or space removed,
spaces turned into hyphens.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.doc_parts import read_parts

_DOC = Path(__file__).resolve().parents[1] / "docs" / "architecture.md"
_TOC_LINK_RE = re.compile(r"^(  )?- \[(.+)\]\(((?:architecture-teil-\d+\.md)?)#([^)]+)\)$")


def _slug(text: str) -> str:
    return re.sub(r"[^\w\- ]", "", text.lower()).replace(" ", "-")


def _toc() -> list[str]:
    text = _DOC.read_text(encoding="utf-8")
    start = text.index("**Inhalt**\n\n")
    return text[start:].split("\n\n", 2)[1].splitlines()


def test_toc_lists_every_heading_in_order() -> None:
    expected = []
    for number, (name, text) in enumerate(read_parts(_DOC), 1):
        target = "" if number == 1 else name
        body = re.sub(r"```.*?```", "", text, flags=re.S)
        for level, title in re.findall(r"^(##|###) (.+)$", body, flags=re.M):
            expected.append(("  " if level == "###" else "") + f"- [{title}]({target}#{_slug(title)})")
    assert _toc() == expected


def test_toc_links_are_well_formed() -> None:
    toc_lines = _toc()
    assert toc_lines
    for line in toc_lines:
        match = _TOC_LINK_RE.match(line)
        assert match, line
        assert match.group(4) == _slug(match.group(2))
