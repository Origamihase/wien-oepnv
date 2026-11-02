"""Read Markdown files that are split into parts (700-line limit).

Since 2026-11-02 no file may have more than 700 lines. A long page
``docs/x.md`` keeps its start, the rest follows in ``docs/x-teil-2.md``,
``docs/x-teil-3.md``, ... next to it. Every later part starts with the line
``[<Titel>](x.md), Teil n.`` and every part except the last ends with
``Weiter in [Teil n+1](x-teil-<n+1>.md).`` Tests that read such a page use
:func:`read_doc` to get the whole text in order.
"""

from __future__ import annotations

import re
from pathlib import Path

_PART_RE = re.compile(r"-teil-(\d+)\.md$")


def part_paths(doc: Path) -> list[Path]:
    """``doc`` and its parts, in order."""
    parts = [p for p in doc.parent.glob(f"{doc.stem}-teil-*.md") if _PART_RE.search(p.name)]
    parts.sort(key=lambda p: int(_PART_RE.search(p.name)[1]))  # type: ignore[index]
    return [doc, *parts]


def _body(path: Path, number: int, last: bool) -> str:
    lines = path.read_text(encoding="utf-8").rstrip("\n").split("\n")
    if number > 1:
        lines = lines[2:]
    if not last:
        lines = lines[:-2]
    return "\n".join(lines)


def read_doc(doc: Path) -> str:
    """The whole text of ``doc`` and its parts, without the part links."""
    paths = part_paths(doc)
    return "\n\n".join(_body(p, i, i == len(paths)) for i, p in enumerate(paths, 1)) + "\n"


def read_parts(doc: Path) -> list[tuple[str, str]]:
    """``(file name, text without part links)`` for ``doc`` and each part."""
    paths = part_paths(doc)
    return [(p.name, _body(p, i, i == len(paths))) for i, p in enumerate(paths, 1)]
