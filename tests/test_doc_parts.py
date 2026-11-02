"""Split Markdown pages form one chain (700-line limit since 2026-11-02).

A page ``x.md`` that is too long continues in ``x-teil-2.md``,
``x-teil-3.md``, ... in the same folder (``tests/doc_parts.py``). Every
later part names the first one and its number on top, every part except the
last links the next one at the end, and the numbers have no gap. Then a
reader on GitHub or on the website can walk from the start to the end.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from tests.doc_parts import part_paths

_ROOT = Path(__file__).resolve().parents[1]
_PART_RE = re.compile(r"^(.+)-teil-(\d+)\.md$")


def _split_pages() -> list[Path]:
    git = shutil.which("git")
    assert git is not None, "git fehlt"
    argv = [git, "ls-files", "--cached", "--others", "--exclude-standard", "*.md"]
    out = subprocess.run(argv, cwd=_ROOT, check=True, capture_output=True, text=True, timeout=60).stdout  # noqa: S603
    firsts = set()
    for name in out.split():
        match = _PART_RE.match(name)
        if match:
            firsts.add(_ROOT / f"{match[1]}.md")
    return sorted(firsts)


def test_every_part_has_its_first_page() -> None:
    pages = _split_pages()
    assert pages, "keine aufgeteilte Seite gefunden"
    for page in pages:
        assert page.is_file(), f"{page} fehlt zu seinen Teilen"


def test_parts_are_chained_without_gap() -> None:
    for page in _split_pages():
        paths = part_paths(page)
        numbers = [int(_PART_RE.match(p.name)[2]) for p in paths[1:]]  # type: ignore[index]
        assert numbers == list(range(2, len(paths) + 1)), f"{page.name}: Lücke in den Teilnummern"
        for number, path in enumerate(paths, 1):
            lines = path.read_text(encoding="utf-8").rstrip("\n").split("\n")
            if number > 1:
                assert lines[0].endswith(f"]({page.name}), Teil {number}."), f"{path.name}: Kopfzeile"
                assert lines[1] == ""
            if number < len(paths):
                assert lines[-1] == f"Weiter in [Teil {number + 1}]({paths[number].name}).", f"{path.name}: Fußzeile"
