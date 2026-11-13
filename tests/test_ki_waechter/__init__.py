# ruff: noqa: S603, S607
"""KI-Wächter (scripts/ki_waechter.py): input, Gemini call, mechanical check of every finding, report.

Operator decision 2026-10-31 (Michael): an AI watches the feed and reports,
it never rewrites; "Falsche Funde stören mich am meisten" (2026-10-30). So a
finding counts only when its quotes stand verbatim in the feed and, for a
wrong fact, in the source; a duplicate only when both items stood in the feed
at the same time. The same day: every item text of the last 24 hours, and
Gemini through the free API tier. These tests keep that gate closed.
"""

# Aufgeteilt am 2026-11-02 (höchstens 700 Zeilen je Datei): Hilfen und Fixtures in
# _support.py, die Tests in test_teil_<n>.py in ihrer alten Reihenfolge. Seit 2026-11-13
# stehen die Tests aus test_teil_1.py nach Thema in test_eingabe.py, test_funde.py,
# test_bericht.py, test_gemini.py und test_prompt.py.
