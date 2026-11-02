"""Tests for the README-snapshot patcher in ``generate_markdown_stats.py``.

The patcher is a separate write boundary from the
``docs/statistik.md`` dashboard:

* The dashboard is full-history, byte-identical for the same input,
  and consumed by humans via the docs site.
* The README block is a 30-day rolling snapshot that GitHub renders on
  the public landing page; it is patched **in place** between named
  ``<!-- STATS:* -->`` markers so the surrounding hand-authored README
  content survives every workflow run untouched.

These tests guard the boundary on three axes that the dashboard tests
do not cover:

1. **Idempotency** — a second run with identical input must not touch
   the file (mtime preservation matters because the auto-commit action
   uses content equality, not timestamps).
2. **Marker invariants** — missing markers, a single half of a pair,
   markers inside a fenced code block, and unicode spacing between
   markers must all leave the surrounding README byte-stable.
3. **Markdown-injection at the render boundary** — the ``location_name``
   column is interpolated into a 3-column table; a CSV row carrying a
   literal ``|`` / ``` ` ``` / ``<`` must not break out of the cell.
"""

# Aufgeteilt am 2026-11-02 (höchstens 700 Zeilen je Datei): Hilfen und Fixtures in
# _support.py, die Tests in test_teil_<n>.py in ihrer alten Reihenfolge.
