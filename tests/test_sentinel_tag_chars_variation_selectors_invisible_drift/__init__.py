"""Sentinel PoC: the canonical Trojan-Source / invisible-character regex
union and its seven sibling validators do NOT cover the **Unicode Tag
block** (``U+E0000``-``U+E007F``) and the **Variation Selectors**
(``U+FE00``-``U+FE0F`` plus the supplementary ``U+E0100``-``U+E01EF``).

The Tag block is the canonical ``ChatGPT prompt injection`` /
``invisible-data-smuggling`` primitive — every ASCII printable code
point ``\\x20``-``\\x7E`` has a paired Tag character in
``\\U000e0020``-``\\U000e007E`` that renders as **zero-width** in every
modern terminal / browser / PDF reader / GitHub Web UI / IDE preview /
RSS feed reader. ``U+E007F`` is CANCEL TAG, the documented terminator
of a Tag run. ``U+E0001`` is the deprecated LANGUAGE TAG primitive.

Variation Selectors are 4-bit-payload steganographic primitives — VS1
(``U+FE00``)-VS16 (``U+FE0F``) live in the BMP and VS17 (``U+E0100``)
-VS256 (``U+E01EF``) live in plane 14 alongside the Tag block. Both
classes are documented invisible-character primitives in Unicode TR9
(BiDi) and TR50 (Variation Selectors) — Apple's emoji renderer is the
only legitimate consumer of VS-15 / VS-16 (text vs. emoji
presentation), and EVERY OTHER use is steganographic data hiding.

The four orthogonal sibling sinks share a single drift family — each
carries its own re.compile of the canonical attack-byte union, and
each one stops at the BiDi-isolate band (``\\u2069``) followed by BOM
(``\\ufeff``). None extends into plane 14 to cover the Tag block, and
none covers the BMP Variation Selector band.

Threat model
============

Single-byte-tag-character payloads are *invisible* in every modern
operator-facing renderer:

1. **Cache JSON sidecars** — A planted upstream payload (provider
   compromise / OSM Overpass cache poisoning / VOR ReST hijack /
   compromised CI runner) containing
   ``"name": "Hauptbahnhof\\U000e0020\\U000e0065\\U000e0076\\U000e0069\\U000e006c"``
   (visually rendered ``Hauptbahnhof`` — the tag-character ``"·evil"``
   suffix is invisible) survives
   :func:`src.utils.serialize.scrub_trojan_source_primitives` because
   the canonical attack-byte union ``_TROJAN_SOURCE_PRIMITIVES_RE``
   does NOT match plane-14 Tag code points. The poisoned name then
   reaches ``cache/<provider>/events.json``, ``data/stations.json``,
   and the weekly committed git diff that the operator reviews — the
   tag-suffix is invisible in ``git log -p`` / ``git show`` / the
   GitHub web UI / ``cat`` / ``less`` / IDE preview.
2. **Operator-facing log lines** — A hostile upstream exception text
   carrying tag-character payloads (planted in an HTTP error body,
   smuggled through an upstream JSON field, MITM-injected) flows
   through :func:`src.utils.logging.sanitize_log_message`. Pre-fix
   the always-strip floor ``_INVISIBLE_DANGEROUS_RE`` does NOT match
   the tag bytes — they land verbatim in ``log/diagnostics.log``,
   ``docs/feed_health.json`` (a public artefact published to GitHub
   Pages), and the GitHub Issue body auto-submitted on every fail.
3. **Public RSS feed** — Provider titles / descriptions / time-lines
   are routed through ``src/build_feed.py:_sanitize_text`` ->
   ``_CONTROL_RE``. Tag bytes survive, land inside ``<title>`` /
   ``<description>`` of ``docs/feed.xml``, and reach every subscribed
   RSS reader.
4. **Stations directory** — ``data/stations.json`` entries flow
   through ``stations_validation._UNSAFE_CHARS_RE``. Tag bytes
   smuggled into ``name`` / ``aliases`` / ``bst_code`` / ``vor_id``
   slip past the validator and reach the published feed item that
   keys off the station name.
5. **Outbound HTTP URL boundary** — ``src/utils/http.py``
   ``_UNSAFE_URL_CHARS`` validates URLs before redirect / fetch /
   feed-link emission. Pre-fix a planted feed-item ``link`` with
   tag-character payloads
   (``https://safe.example.com/path\\U000e0021\\U000e0065\\U000e0076\\U000e0069\\U000e006c``
   — visually identical to ``https://safe.example.com/path`` but
   different bytes for cache-key / equality / GUID-collision shapes)
   passes the validator unmodified.
6. **Spreadsheet / CSV stats ledgers** — Tag-character payloads in
   provider names / location names planted via a poisoned cache
   survive ``_CSV_CONTROL_CHARS_RE`` and land in
   ``data/stats/<kind>_YYYY.csv``. Operators opening the CSV in
   Excel / LibreOffice Calc / Google Sheets see clean-looking text;
   downstream pivot-table aggregation keys on the **invisible**
   tag-character variants as distinct cells from the visible cousin,
   silently fracturing the operator's analytics view.
7. **Markdown sinks** — ``data/feed_health.md``, ``docs/statistik.md``,
   GitHub Issue bodies render Markdown via
   ``src/utils/text.py:_MARKDOWN_NORMALISE_UNSAFE_RE``. Tag bytes
   survive and reach the rendered Markdown verbatim.

Variation Selectors (``U+FE00``-``U+FE0F`` + ``U+E0100``-``U+E01EF``)
share every threat above PLUS the additional steganography surface:
each VS character carries a 4-bit payload (16 BMP selectors + 240
supplementary selectors = 4 + 8 = 12 bits per visible code point in
the "Sneaky Text" technique), enabling silent data exfiltration when
the visible text is copied from a log / RSS feed / GitHub Issue body
into an attacker-controlled service.

Severity
========
**HIGH** — Trojan-Source primitive (CVE-2021-42574-class display
confusion) AND prompt-injection primitive (documented in OpenAI's
2024 Tag-character disclosure: ASCII-tags surviving sanitisation
flow into downstream LLM training / RAG ingestion / chat copy-paste
loops). Public artefact (``docs/feed.xml`` / ``docs/feed_health.json``
/ ``data/stations.json``) and operator-facing log surface (cron commit
diff + Issue body). Defence-in-depth gap on the documented "always
-strip floor" pinned by the audit across 14 prior rounds.

Fix
===
Widen every sibling regex to include the three new ranges:

  * ``\\ufe00-\\ufe0f`` — VARIATION SELECTOR 1-16 (BMP)
  * ``\\U000e0000-\\U000e007f`` — Unicode Tag block
  * ``\\U000e0100-\\U000e01ef`` — VARIATION SELECTOR 17-256 (plane 14)

Every test in this file fails pre-fix and passes post-fix. The
inventory invariants (``test_*_regex_covers_canonical_invisible_dangerous_set``)
extend the existing pinning rule: any future widening of the canonical
sanitiser MUST be reflected in every sibling validator or the test
fails until the drift is closed.
"""

# Aufgeteilt am 2026-11-02 (höchstens 700 Zeilen je Datei): Hilfen und Fixtures in
# _support.py, die Tests in test_teil_<n>.py in ihrer alten Reihenfolge.
