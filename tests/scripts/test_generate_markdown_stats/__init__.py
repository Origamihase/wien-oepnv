"""Tests for ``scripts/generate_markdown_stats.py``.

The aggregator is exercised end-to-end through CSV files written into a
``tmp_path`` directory — that mirrors the production read path exactly
(``csv.reader`` on real on-disk bytes) without coupling tests to any
mock-CSV abstraction. Where helpful, individual rendering helpers are
also called directly so a single regression in scaling logic shows up
as a focused failure rather than a giant whole-dashboard diff.
"""

# Aufgeteilt am 2026-11-02 (höchstens 700 Zeilen je Datei): Hilfen und Fixtures in
# _support.py, die Tests in test_teil_<n>.py in ihrer alten Reihenfolge.
