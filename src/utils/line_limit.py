"""The 700-line rule: which files count, how lines are counted, when to split.

Michael's rule of 2026-11-02: no text file has more than 700 lines
(``tests/test_file_line_limit.py``). His decision of 2026-11-13: a file at
the limit is split by topic, it is not kept below the limit by shortening
comments or joining lines (``scripts/check_line_limit_edits.py``). Both
checks read their numbers and their idea of a counted file from here.
"""

from __future__ import annotations

from pathlib import Path

from .files import read_capped_bytes

__all__ = [
    "EXEMPT_PREFIXES", "LIMIT", "MAX_COUNTED_FILE_BYTES", "SPLIT_FROM", "SPLIT_TO", "SUFFIXES",
    "count_lines", "count_lines_bytes", "is_counted",
]

#: No counted file has more lines than this.
LIMIT = 700
#: A file with at least this many lines is "at the limit": the next change
#: that adds a line to it has to split it.
SPLIT_FROM = 690
#: After that change the file has at most this many lines. The gap to
#: ``SPLIT_FROM`` is what tells a split from a trim: nobody shortens 140
#: lines of comments to make room for one new line.
SPLIT_TO = 550
SUFFIXES = frozenset(
    {".py", ".pyi", ".js", ".mjs", ".css", ".html", ".md", ".yml", ".yaml", ".sh", ".toml", ".cfg", ".ini", ".txt"}
)
EXEMPT_PREFIXES = ("data/", "cache/")
#: Read cap for one counted file. A text file of 700 lines has some tens of
#: KiB; a larger file than this is no file the rule can pass.
MAX_COUNTED_FILE_BYTES = 16 * 1024 * 1024


def is_counted(path: str) -> bool:
    """Whether the rule applies to the repository path ``path``."""
    if path.startswith(EXEMPT_PREFIXES):
        return False
    return Path(path).suffix.lower() in SUFFIXES


def count_lines_bytes(data: bytes) -> int:
    """Count lines like ``wc -l`` plus a last line without newline."""
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


def count_lines(path: Path) -> int:
    """Count the lines of the file at ``path`` (see ``count_lines_bytes``).

    Raises ``ValueError`` for a file that is missing, unreadable or larger
    than ``MAX_COUNTED_FILE_BYTES``: no count is no pass.
    """
    data = read_capped_bytes(path, MAX_COUNTED_FILE_BYTES, label="line limit")
    if data is None:
        raise ValueError(f"{path}: nicht lesbar oder größer als {MAX_COUNTED_FILE_BYTES} Bytes")
    return count_lines_bytes(data)
