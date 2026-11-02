"""The changelog lives in parts under ``changelog/``; CHANGELOG.md is the index.

Since 2026-11-02 no file may have more than 700 lines
(``tests/test_file_line_limit.py``). The changelog grows by about 260 lines a
day, so its entries live in month parts: ``changelog/<JJJJ-MM>-teil-<n>.md``,
part 1 the oldest. ``CHANGELOG.md`` only lists the parts. This test keeps the
index and the folder in step and stops entries from landing in the index.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_INDEX = _ROOT / "CHANGELOG.md"
_DIR = _ROOT / "changelog"
_PART_RE = re.compile(r"^(\d{4})-(\d{2})-teil-(\d+)\.md$")
_LINK_RE = re.compile(r"\]\(changelog/([^)]+)\)")


def _links() -> list[str]:
    return _LINK_RE.findall(_INDEX.read_text(encoding="utf-8"))


def test_index_links_every_part_once_and_nothing_else() -> None:
    links = _links()
    assert len(links) == len(set(links)), "Ein Teil steht doppelt in CHANGELOG.md"
    on_disk = sorted(p.name for p in _DIR.glob("*.md"))
    assert sorted(links) == on_disk


def test_index_holds_no_entries() -> None:
    text = _INDEX.read_text(encoding="utf-8")
    entries = [line for line in text.splitlines() if line.startswith("* ")]
    assert not entries, "Einträge gehören in den höchsten Teil des Monats unter changelog/:\n" + "\n".join(entries)


def test_parts_are_numbered_without_gaps_and_listed_newest_first() -> None:
    by_month: dict[str, list[int]] = {}
    for name in _links():
        match = _PART_RE.match(name)
        if match is None:
            continue
        by_month.setdefault(f"{match[1]}-{match[2]}", []).append(int(match[3]))
    assert by_month
    for month, numbers in by_month.items():
        assert numbers == sorted(numbers, reverse=True), f"{month}: Teile nicht absteigend gelistet"
        assert sorted(numbers) == list(range(1, len(numbers) + 1)), f"{month}: Lücke in den Teilnummern"


def test_every_part_starts_with_its_heading() -> None:
    for path in _DIR.glob("*.md"):
        first = path.read_text(encoding="utf-8").splitlines()[0]
        assert first.startswith("# Changelog "), path.name
        match = _PART_RE.match(path.name)
        if match is not None:
            assert first.endswith(f", Teil {int(match[3])}"), path.name
