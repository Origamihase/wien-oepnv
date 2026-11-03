"""Regression tests for proper-noun preservation in the EN feed pipeline.

The Helsinki opus-mt-de-en translator routinely mistranslates Vienna
transit proper nouns ("Stephansplatz" → "Stephen's Square",
"Wiener Linien" → "Vienna Lines"). :func:`src.feed_build.masking._mask_entities`
replaces every known brand / station / line identifier with a stable
``XENT<n>X`` placeholder before inference, and
:func:`src.feed_build.masking._unmask_entities` restores the original surface
form afterwards. These tests pin the mask/unmask contract so future
refactors cannot silently break the round-trip.
"""

# Aufgeteilt am 2026-11-02 (höchstens 700 Zeilen je Datei): Hilfen und Fixtures in
# _support.py, die Tests in test_teil_<n>.py in ihrer alten Reihenfolge.
