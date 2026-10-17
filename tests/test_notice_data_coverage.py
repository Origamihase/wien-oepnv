"""Every file under ``data/`` has a licence line in ``NOTICE.md``.

The licence audit of 2026-10-17 found files that ``NOTICE.md`` did not name
(``data/raw/``, the feed state files) and one that it named under the wrong
source (``data/gtfs/stops.txt``, a VOR extract listed as ÖBB-Infrastruktur).
``data/`` is published with the repository, so a new file without a line in
``NOTICE.md`` would go out without its source and licence. Michael decided on
2026-10-16 that the code stays MIT and every dataset keeps its own licence
(see ``NOTICE.md``); this test holds the inventory to that rule.
"""

from __future__ import annotations

import re
import shutil
import subprocess  # noqa: S404 - fixed git command, no user input
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]

# Files that carry no third-party content: documentation of the folder
# itself, git housekeeping and the download caches' ETag sidecars.
_SKIP_NAMES = {"README.md", ".gitignore"}
_SKIP_SUFFIXES = (".cache.json",)


def _tracked_data_files() -> list[str]:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git not available")
    argv = [git, "ls-files", "data"]
    proc = subprocess.run(argv, cwd=_ROOT, capture_output=True, text=True, check=False)  # noqa: S603
    if proc.returncode != 0 or not proc.stdout.strip():
        pytest.skip("not a git checkout")
    files = []
    for line in proc.stdout.splitlines():
        name = line.rsplit("/", 1)[-1]
        if name in _SKIP_NAMES or name.endswith(_SKIP_SUFFIXES):
            continue
        files.append(line)
    return files


def _notice_patterns() -> list[str]:
    text = (_ROOT / "NOTICE.md").read_text(encoding="utf-8")
    # The bare folder name (``data/``) appears in headings and covers nothing.
    return [p for p in re.findall(r"`([^`\s]+)`", text) if p.startswith("data/") and p != "data/"]


def _covered(path: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if pattern.endswith("/"):
            if path.startswith(pattern):
                return True
            continue
        # ``*`` stays inside one folder, so ``data/raw/wl/*`` does not cover
        # ``data/raw/wl/sub/file``.
        regex = "".join("[^/]*" if ch == "*" else re.escape(ch) for ch in pattern)
        if re.fullmatch(regex, path):
            return True
    return False


def test_every_data_file_is_named_in_notice() -> None:
    patterns = _notice_patterns()
    missing = [path for path in _tracked_data_files() if not _covered(path, patterns)]
    assert not missing, f"NOTICE.md names no source/licence for: {missing}"


def test_vor_stop_data_is_not_called_open() -> None:
    # The VOR stop list came from the AnachB webapp's backend, not from an
    # open dataset; NOTICE.md called it CC BY 4.0 until 2026-10-17.
    text = (_ROOT / "NOTICE.md").read_text(encoding="utf-8")
    row = next(line for line in text.splitlines() if "`data/vor-haltestellen.csv`" in line and line.startswith("|"))
    assert "CC BY" not in row
    assert "ungeklärt" in row
