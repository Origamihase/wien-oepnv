#!/usr/bin/env python3
"""A file at the 700-line limit is split, not trimmed.

The 700-line rule (``tests/test_file_line_limit.py``) counts lines. Between
2026-11-03 and 2026-11-13 the number of files with exactly 700 lines went
from 1 to 14: changes to them added lines and removed the same number
elsewhere in the file, mostly by shortening comments and joining lines
(``.github/workflows/update-cycle.yml``: twelve lines in, twelve out, nine
lines of reasoning became two). The files stayed below the limit and became
harder to read. Michael's decision of 2026-11-13: such a file is split by
topic.

This gate compares the work tree with a base revision. It fails for every
counted file that

* had at least ``SPLIT_FROM`` (690) lines at the base,
* still has at least ``SPLIT_FROM`` lines now, and
* got at least one added line.

So a file may grow into the range 690 to 700, but the next change that adds
a line to it has to split it. Removing lines only is allowed. The gate
cannot be met by shortening: a change that adds a line and leaves the file
at 690 or more fails whatever else it removes.

The base is ``--base`` (CI: ``HEAD^1``, the base of the pull request's merge
commit and the previous commit of a push), by default the merge base of
``HEAD`` and ``origin/main``.

Run locally before a commit:
    python scripts/check_line_limit_edits.py

Exit codes:
    0  — no file at the limit was edited without a split
    1  — at least one such file
    2  — no measurement (git missing, base revision unknown, git failed)
"""
from __future__ import annotations

import argparse
import shutil
import subprocess  # nosec B404
import sys
from dataclasses import dataclass
from pathlib import Path

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.line_limit import LIMIT, SPLIT_FROM, count_lines, count_lines_bytes, is_counted  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
GIT_TIMEOUT_SECONDS = 60
DEFAULT_MAIN = "origin/main"


class MeasurementError(RuntimeError):
    """git gave no trustworthy answer, so the gate must not pass."""


@dataclass(frozen=True)
class Finding:
    """A file at the limit that was edited and not split."""

    path: str
    base_lines: int
    lines: int
    added: int


def _git(root: Path, *args: str) -> bytes:
    git = shutil.which("git")
    if git is None:
        raise MeasurementError("git fehlt")
    try:
        proc = subprocess.run(  # noqa: S603  # nosec B603
            [git, *args], cwd=root, capture_output=True, timeout=GIT_TIMEOUT_SECONDS, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MeasurementError(f"git {args[0]} lief nicht: {type(exc).__name__}") from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise MeasurementError(f"git {args[0]} endete mit {proc.returncode}: {detail[-1] if detail else ''}")
    return proc.stdout


def resolve_base(root: Path, base: str | None) -> str:
    """Return the commit the work tree is compared with."""
    if base is None:
        return _git(root, "merge-base", "HEAD", DEFAULT_MAIN).decode("ascii").strip()
    return _git(root, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}").decode("ascii").strip()


def _added_lines(root: Path, base: str) -> dict[str, int]:
    """Added lines per path between ``base`` and the work tree.

    ``--no-renames``: a renamed file is a new path, its old path is gone.
    Binary files report ``-`` and are left out (they are not counted files).
    """
    out = _git(root, "diff", "--numstat", "--no-renames", "-z", base, "--").decode("utf-8", "surrogateescape")
    added: dict[str, int] = {}
    for record in out.split("\0"):
        if not record:
            continue
        parts = record.split("\t", 2)
        if len(parts) != 3:
            raise MeasurementError(f"git diff --numstat: unlesbare Zeile {record!r}")
        plus, _minus, path = parts
        if plus == "-":
            continue
        if not plus.isascii() or not plus.isdigit():
            raise MeasurementError(f"git diff --numstat: unlesbare Zahl {plus!r}")
        added[path] = int(plus)
    return added


def _base_lines(root: Path, base: str, path: str) -> int:
    """Lines of ``path`` at ``base``; 0 when the file did not exist there."""
    listed = _git(root, "ls-tree", "-z", base, "--", path)
    if not listed:
        return 0
    return count_lines_bytes(_git(root, "cat-file", "blob", f"{base}:{path}"))


def find_unsplit_edits(root: Path, base: str) -> list[Finding]:
    """Every counted file at the limit that got added lines and no split."""
    findings: list[Finding] = []
    for path, added in sorted(_added_lines(root, base).items()):
        if added == 0 or not is_counted(path):
            continue
        current = root / path
        if not current.is_file():
            continue
        lines = count_lines(current)
        if lines < SPLIT_FROM:
            continue
        base_lines = _base_lines(root, base, path)
        if base_lines < SPLIT_FROM:
            continue
        findings.append(Finding(path, base_lines, lines, added))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--base", help=f"Vergleichsstand (Vorgabe: gemeinsamer Vorfahre von HEAD und {DEFAULT_MAIN})")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        base = resolve_base(args.root, args.base)
        findings = find_unsplit_edits(args.root, base)
    except MeasurementError as exc:
        print(f"Zeilengrenze, teilen statt kürzen: keine Messung ({exc})", file=sys.stderr)
        return 2
    if not findings:
        print(f"Zeilengrenze, teilen statt kürzen: keine Datei ab {SPLIT_FROM} Zeilen ohne Teilung geändert.")
        return 0
    print(
        f"Dateien am Anschlag (ab {SPLIT_FROM} von {LIMIT} Zeilen) geändert, ohne sie zu teilen.\n"
        "Nach Themen aufteilen (docs/modularisierung.md), nicht Kommentare kürzen oder Zeilen zusammenziehen:",
        file=sys.stderr,
    )
    for f in findings:
        print(f"  {f.path}: vorher {f.base_lines}, jetzt {f.lines} Zeilen, {f.added} neue", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
