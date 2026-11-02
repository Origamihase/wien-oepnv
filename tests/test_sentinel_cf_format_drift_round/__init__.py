"""Sentinel PoC: the canonical Trojan-Source / invisible-character regex
union and its eight sibling validators do NOT cover **44 Unicode Format
characters** (general category ``Cf``) across **13 disjoint code-point
bands** that Unicode classifies as zero-width / invisible-by-default
formatting primitives in the same family as ZWSP/ZWNJ/ZWJ already in
the canonical floor.

The canonical floor pinned by every prior round (``_INVISIBLE_DANGEROUS_RE``
in ``src/utils/logging.py``, plus the eight sibling validators) covers
~119 of the 163 ``Cf``-class code points in Unicode 16.0. The 44 missing
code points span the following 13 bands:

  *  ``U+00AD``                 — SOFT HYPHEN (the canonical "invisible-by-
                                  default" character used in countless
                                  homograph / steganography attacks; only
                                  rendered at line-break opportunities).
  *  ``U+0600``-``U+0605``      — ARABIC NUMBER SIGN, ARABIC SIGN SANAH,
                                  ARABIC FOOTNOTE MARKER, ARABIC SIGN SAFHA,
                                  ARABIC SIGN SAMVAT, ARABIC NUMBER MARK
                                  ABOVE (zero-width Arabic prefix marks).
  *  ``U+06DD``                 — ARABIC END OF AYAH.
  *  ``U+070F``                 — SYRIAC ABBREVIATION MARK.
  *  ``U+0890``-``U+0891``      — ARABIC POUND / PIASTRE MARK ABOVE.
  *  ``U+08E2``                 — ARABIC DISPUTED END OF AYAH.
  *  ``U+206A``-``U+206F``      — Deprecated BiDi formatting controls
                                  (INHIBIT/ACTIVATE SYMMETRIC SWAPPING,
                                  INHIBIT/ACTIVATE ARABIC FORM SHAPING,
                                  NATIONAL/NOMINAL DIGIT SHAPES).
  *  ``U+FFF9``-``U+FFFB``      — INTERLINEAR ANNOTATION ANCHOR /
                                  SEPARATOR / TERMINATOR.
  *  ``U+110BD``                — KAITHI NUMBER SIGN.
  *  ``U+110CD``                — KAITHI NUMBER SIGN ABOVE.
  *  ``U+13430``-``U+13438``    — EGYPTIAN HIEROGLYPH JOINERS / SEGMENT
                                  formatting controls.
  *  ``U+1BCA0``-``U+1BCA3``    — SHORTHAND FORMAT LETTER OVERLAP /
                                  CONTINUING OVERLAP / DOWN STEP / UP STEP.
  *  ``U+1D173``-``U+1D17A``    — MUSICAL SYMBOL BEGIN / END BEAM / TIE /
                                  SLUR / PHRASE.

The structural invariant that justifies the additive widening: every
code point above is in Unicode general category ``Cf`` (Format) — the
same category as ZWSP/ZWNJ/ZWJ already covered by the floor. None has
any legitimate consumer in this codebase's data path (no Arabic /
Syriac / Egyptian-hieroglyph / shorthand / musical-notation content
flows through any provider feed, station name, sitemap URL, RSS title
or operator log line).

Threat model
============

The 13 missing bands are documented attack primitives across the seven
sinks the codebase already hardens against the Tag block / Variation
Selector / WJ family. The most impactful sub-families:

1. **U+00AD SOFT HYPHEN** is the single most dangerous omission:
   * Renders zero-width *unconditionally* in browsers / RSS readers /
     terminals / IDE preview / GitHub web UI when not at a line-break
     opportunity, but is **stored as a real character** in every
     downstream byte-equality / hash / GUID dedup key.
   * Has been used in real-world attacks since at least 2018
     (e.g. CVE-2018-19165 in IDN homographs, CVE-2021-43616 in npm
     package-name spoofing, the 2023 "Sneaky Text" research).
   * A planted upstream payload ``"Hbf­evil"`` (SOFT HYPHEN between
     visible tokens) reaches the published RSS feed as
     ``<title>Hbf­evil</title>`` and renders identical to ``Hbfevil``
     in every operator-facing report — but byte-distinct for cache
     keys, GUID dedup, and the autosubmitted GitHub Issue body the
     sentinel protects against impersonation.

2. **U+0600-U+0605, U+06DD, U+070F, U+0890-U+0891, U+08E2** are
   zero-width Arabic / Syriac formatting marks. Per Unicode UAX #9
   they have zero advance width when not preceded by a digit, so a
   planted ``"VOR{U+0604}error"`` at the front of an upstream
   exception text renders identical to ``"VORerror"`` in every
   RSS reader / Markdown sink / log line. None has a legitimate
   consumer in this codebase.

3. **U+206A-U+206F** are *deprecated* BiDi formatting controls per
   Unicode (INHIBIT/ACTIVATE SYMMETRIC SWAPPING, INHIBIT/ACTIVATE
   ARABIC FORM SHAPING, NATIONAL/NOMINAL DIGIT SHAPES). They render
   zero-width in every modern terminal / browser / RSS reader. The
   existing canonical floor already strips ``U+2060``-``U+2069``
   (the WJ + Invisible-* + BiDi-isolate band); folding in
   ``U+206A``-``U+206F`` to ``U+2060``-``U+206F`` closes the
   adjacent deprecated-controls band.

4. **U+FFF9-U+FFFB** (INTERLINEAR ANNOTATION ANCHOR / SEPARATOR /
   TERMINATOR) are Unicode "ruby annotation" formatting controls.
   They render zero-width except in dedicated CJK ruby renderers
   (no consumer in the Vienna ÖPNV pipeline). A planted payload
   ``"Wien Hbf{U+FFF9}{U+FFFB}phishing"`` smuggles steganographic
   bytes through the dedup key while rendering identical in the
   feed UI.

5. **U+110BD, U+110CD, U+13430-U+13438, U+1BCA0-U+1BCA3,
   U+1D173-U+1D17A** are supplementary-plane Format characters from
   the Kaithi / Egyptian Hieroglyphs / Shorthand / Musical Symbols
   blocks. None has a legitimate consumer in this codebase's
   ÖPNV-Vienna data path. They render zero-width in every
   non-specialist renderer, are documented zero-width in Unicode,
   and serve as additional steganography alphabet bytes that defeat
   any "strip BMP-only" filter that does not enumerate the
   supplementary planes.

Public sinks impacted
=====================

  *  ``docs/feed.xml``            — RSS XML serialiser via
                                    ``src/build_feed.py:_CONTROL_RE``.
  *  ``docs/feed_health.json``    — public health report via the
                                    ``_CONTROL_CHARS_RE`` family in
                                    ``src/feed/reporting.py`` and
                                    ``src/utils/logging.py``.
  *  ``docs/feed-health.md``      — operator-facing Markdown report
                                    via
                                    ``src/utils/text.py:_MARKDOWN_NORMALISE_UNSAFE_RE``.
  *  ``docs/sitemap.xml``         — sitemap URLs via
                                    ``scripts/generate_sitemap.py:_UNSAFE_URL_CHARS``.
  *  ``data/stats/<kind>_YYYY.csv``
                                  — pivot-table-aggregated provider /
                                    location names via
                                    ``src/utils/stats.py:_CSV_CONTROL_CHARS_RE``.
  *  ``data/stations.json``       — canonical station directory via
                                    ``src/utils/stations_validation.py:_UNSAFE_CHARS_RE``.
  *  ``cache/<provider>/events.json``
                                  — provider event sidecars via
                                    ``src/utils/serialize.py:_TROJAN_SOURCE_PRIMITIVES_RE``.
  *  ``log/diagnostics.log``      — operator log lines via
                                    ``src/utils/logging.py:sanitize_log_message``.
  *  GitHub Issue body            — auto-submitted on every fail via
                                    ``submit_auto_issue``.
  *  Outbound HTTP URL boundary   — every ``validate_http_url`` call
                                    via ``src/utils/http.py:_UNSAFE_URL_CHARS``.

Severity
========
**HIGH** — visual deception (Trojan-Source-class display confusion via
SOFT HYPHEN especially) + steganographic data smuggling + LLM prompt-
injection smuggling (the 44 code points double the alphabet of
invisible characters available to a smuggling attack vs. the prior
WJ-only round) + cache-key / GUID-collision primitive.

Fix shape
=========
Mirror the 2026-05-14 "Zero-Width Format Drift" round shape: extend
the always-strip floor ``_INVISIBLE_DANGEROUS_RE`` with the 13 new
code-point bands and widen each of the eight sibling validators in
lockstep. The widening is **additive only against the invisible-
character family** — every pre-fix-covered code point still matches
post-fix; legitimate German (ä/ö/ü/Ä/Ö/Ü/ß), CJK, and emoji content
is untouched.

The inventory test
``test_sibling_regex_covers_canonical_invisible_dangerous_set`` in
``tests/test_sentinel_zero_width_invisible_drift/`` provides the
pinned sibling-superset invariant; widening the canonical floor
without widening every sibling immediately fails that test.
"""

# Aufgeteilt am 2026-11-02 (höchstens 700 Zeilen je Datei): Hilfen und Fixtures in
# _support.py, die Tests in test_teil_<n>.py in ihrer alten Reihenfolge.
