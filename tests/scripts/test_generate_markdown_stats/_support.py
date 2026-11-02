"""Hilfen und Fixtures für die Tests in diesem Paket.

Früher in tests/scripts/test_generate_markdown_stats.py.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path
from zoneinfo import ZoneInfo


REPO_ROOT = Path(__file__).resolve().parents[3]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

VIENNA_TZ = ZoneInfo("Europe/Vienna")


# ---- Helpers ---------------------------------------------------------------


def _write_csv(
    path: Path,
    header: tuple[str, ...],
    rows: Sequence[tuple[str, ...]],
) -> None:
    """Helper that writes a small CSV the same way the production writer does.

    *rows* is typed as :class:`Sequence` rather than :class:`list` so
    callers can pass concrete tuple element types (e.g.
    ``list[tuple[str, str, str, str, str]]``) without tripping the
    list-invariance rule mypy enforces under ``--strict``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [",".join(header)]
    for row in rows:
        lines.append(",".join(row))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _stammstrecke_csv(tmp_path: Path, year: int = 2026) -> Path:
    return tmp_path / f"stammstrecke_{year}.csv"


def _stoerungen_csv(tmp_path: Path, year: int = 2026) -> Path:
    return tmp_path / f"stoerungen_{year}.csv"


__all__ = [
    "REPO_ROOT",
    "VIENNA_TZ",
    "_stammstrecke_csv",
    "_stoerungen_csv",
    "_write_csv",
]
