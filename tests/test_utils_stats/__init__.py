"""Tests for ``src.utils.stats``.

Covers the append-only CSV writers and the location-name extraction
heuristic. The writers are exercised against ``tmp_path`` so the
production CSV path is never touched.
"""

# Aufgeteilt am 2026-11-02 (höchstens 700 Zeilen je Datei): Hilfen und Fixtures in
# _support.py, die Tests in test_teil_<n>.py in ihrer alten Reihenfolge.
