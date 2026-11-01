"""Every package appears once per requirements file, ``-r`` includes followed.

On 2026-11-01 a conflict between two Dependabot PRs (#2111 pip-audit, #2115
hypothesis: neighbouring lines) was resolved in the web editor by keeping both
sides. ``requirements-dev.txt`` then carried ``pip-audit>=2.7,<3`` next to
``pip-audit>=2.10.1,<3`` and two hypothesis floors. pip merges such lines, so
nothing failed; the file just no longer said which floor holds. This test
turns the next such merge red on the PR.

Mutation checked: a second ``hypothesis>=6.152.4`` line in
``requirements-dev.txt`` → ``test_no_package_is_listed_twice`` names it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FILES = sorted(ROOT.glob("requirements*.txt"))
_NAME_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")


def _entries(path: Path, seen_files: set[Path] | None = None) -> list[tuple[str, str]]:
    seen_files = seen_files if seen_files is not None else set()
    if path in seen_files:
        return []
    seen_files.add(path)
    out: list[tuple[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith(("-r ", "--requirement ")):
            out.extend(_entries(path.parent / line.split(None, 1)[1], seen_files))
            continue
        match = _NAME_RE.match(line)
        if match:
            name = re.sub(r"[-_.]+", "-", match.group(1)).lower()
            out.append((name, f"{path.name}: {line}"))
    return out


def test_the_files_exist() -> None:
    assert {p.name for p in FILES} >= {"requirements.txt", "requirements-dev.txt"}


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_no_package_is_listed_twice(path: Path) -> None:
    lines: dict[str, list[str]] = {}
    for name, where in _entries(path):
        lines.setdefault(name, []).append(where)
    twice = {name: where for name, where in lines.items() if len(where) > 1}
    assert not twice, f"Package listed more than once: {twice}"
