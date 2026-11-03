"""Paths and the plain environment lookup of the VOR provider.

Split off ``src/providers/vor.py`` (700-line rule, 2026-11-02), which
re-exports these names. ``src.providers.vor`` and ``src.providers.vor_quota``
both read them; this module imports neither, so it sits below the import cycle
between those two.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"


def _get_env(name: str) -> str:
    return (os.getenv(name) or "").strip()


__all__ = [
    "BASE_DIR",
    "DATA_DIR",
    "_get_env",
]
