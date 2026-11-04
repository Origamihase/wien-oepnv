#!/usr/bin/env python3
"""Takt-Fallback: start the Update cycle when IFTTT stays silent for 40 minutes.

Called by ``.github/workflows/takt-fallback.yml``; the logic and the
operator decision (Michael 2026-11-04) are in ``src/takt_fallback.py``.
Never starts a run while IFTTT ticks; exit code 0 in every case it can
handle, so a waiter that ends is never a red run.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.takt_fallback import main

if __name__ == "__main__":
    raise SystemExit(main())
