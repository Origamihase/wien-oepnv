"""Coverage audit for the EN translation pipeline.

After the "feed.en.xml partially translated" bug report, this module
exercises the full ``_format_item_content(lang="en")`` path with a
realistic Wiener Linien disruption sentence containing every entity
class (operator brand, ÖPNV line identifier, station name) plus a
small German clause that the ML model is expected to handle. The
mocked translator simulates the canonical Marian/Helsinki-NLP
behaviour: a word-for-word substitution on the German remainder
that preserves the entity placeholders verbatim.

The asserts pin three invariants:

  (a) Entity preservation: every brand / line / station name appears
      in the final English output **verbatim** (no mistranslation).
  (b) Full-message translation: the German remainder around the
      entities is fully translated; no German connector words
      survive into the rendered EN feed.
  (c) Sticky-German guard: when the pipeline succeeds, the resulting
      cache entry is the real English translation (never the German
      source).
"""

# Aufgeteilt am 2026-11-02 (höchstens 700 Zeilen je Datei): Hilfen und Fixtures in
# _support.py, die Tests in test_teil_<n>.py in ihrer alten Reihenfolge.
