"""Sentinel PoC: the canonical Trojan-Source / invisible-character regex
union and its eight sibling validators do NOT cover the **zero-width
invisible Format characters** ``U+180E`` (MONGOLIAN VOWEL SEPARATOR)
and ``U+2060``-``U+2064`` (WORD JOINER, FUNCTION APPLICATION, INVISIBLE
TIMES, INVISIBLE SEPARATOR, INVISIBLE PLUS).

Every code point listed above is in Unicode General Category ``Cf``
(Format) — the same category as ``U+200B`` (ZWSP), ``U+200C`` (ZWNJ),
``U+200D`` (ZWJ), and the BiDi formatting controls already in the
canonical floor. Each is documented invisible (zero advance width in
every conforming renderer — terminals, browsers, GitHub web UI, IDE
preview, RSS readers, PDF viewers); none has a legitimate consumer in
this codebase's data path.

The current canonical floor (``src/utils/logging.py``
``_INVISIBLE_DANGEROUS_RE`` plus its eight sibling validators) covers
``\\u061c`` (ALM), ``\\u200b``-``\\u200f`` (ZWSP/ZWNJ/ZWJ + LRM/RLM),
``\\u2028``-``\\u202e`` (LSEP/PSEP + LRE/RLE/PDF/LRO/RLO),
``\\u2066``-``\\u2069`` (LRI/RLI/FSI/PDI), ``\\ufeff`` (BOM/ZWNBSP),
``\\ufe00``-``\\ufe0f`` (Variation Selectors 1-16), ``\\U000e0000``-
``\\U000e007f`` (Tag block), and ``\\U000e0100``-``\\U000e01ef``
(Variation Selectors 17-256). It does NOT cover the gap between
``\\u202e`` and ``\\u2066`` — specifically the ``\\u2060``-``\\u2064``
band — nor the legacy ``\\u180e``.

Threat model
============

Zero-width invisible Format characters are a documented attack
primitive in seven orthogonal sinks the codebase already hardens
against the Tag block / Variation Selector family:

1. **Cache JSON sidecars** — A planted upstream payload (provider
   compromise / OSM Overpass cache poisoning / VOR ReST hijack /
   compromised CI runner) carrying
   ``"name": "Hauptbahnhof\\u2060evil"`` (visually rendered
   ``Hauptbahnhofevil`` with zero space — the WJ glues the two tokens
   without producing a visible separator) survives
   :func:`src.utils.serialize.scrub_trojan_source_primitives` because
   the canonical attack-byte union ``_TROJAN_SOURCE_PRIMITIVES_RE``
   does NOT match ``\\u2060``. The poisoned name then reaches
   ``cache/<provider>/events.json``, ``data/stations.json``, and the
   weekly committed git diff — the WJ suffix is invisible in
   ``git log -p`` / ``git show`` / the GitHub web UI / ``cat`` /
   ``less`` / IDE preview.
2. **Operator-facing log lines** — A hostile upstream exception text
   carrying ``\\u2063`` (INVISIBLE SEPARATOR) flows through
   :func:`src.utils.logging.sanitize_log_message`. Pre-fix the
   always-strip floor ``_INVISIBLE_DANGEROUS_RE`` does NOT match
   the byte — it lands verbatim in ``log/diagnostics.log``,
   ``docs/feed_health.json`` (a public artefact published to GitHub
   Pages), and the GitHub Issue body auto-submitted on every fail.
3. **Public RSS feed** — Provider titles / descriptions / time-lines
   are routed through ``src/build_feed.py:_sanitize_text`` ->
   ``_CONTROL_RE``. WJ / Invisible Times / Invisible Separator bytes
   survive, land inside ``<title>`` / ``<description>`` of
   ``docs/feed.xml``, and reach every subscribed RSS reader.
4. **Stations directory** — ``data/stations.json`` entries flow
   through ``stations_validation._UNSAFE_CHARS_RE``. WJ bytes
   smuggled into ``name`` / ``aliases`` / ``bst_code`` / ``vor_id``
   slip past the validator and reach the published feed item that
   keys off the station name. Aliases like ``"Wien Hbf\\u2060"`` and
   ``"Wien Hbf"`` aggregate as DISTINCT entries downstream because
   the dedup key is byte-equality.
5. **Outbound HTTP URL boundary** — ``src/utils/http.py``
   ``_UNSAFE_URL_CHARS`` validates URLs before redirect / fetch /
   feed-link emission. Pre-fix a planted feed-item ``link`` with
   ``\\u2060`` payloads
   (``https://safe.example.com/path\\u2060segment`` — visually
   identical to ``https://safe.example.com/pathsegment`` but
   different bytes for cache-key / equality / GUID-collision shapes)
   passes the validator unmodified.
6. **Spreadsheet / CSV stats ledgers** — Zero-width payloads in
   provider names / location names planted via a poisoned cache
   survive ``_CSV_CONTROL_CHARS_RE`` and land in
   ``data/stats/<kind>_YYYY.csv``. Operators opening the CSV in
   Excel / LibreOffice Calc / Google Sheets see clean-looking text;
   downstream pivot-table aggregation keys on the **invisible**
   variants as distinct cells from the visible cousin, silently
   fracturing the operator's analytics view.
7. **Markdown sinks** — ``data/feed_health.md``, ``docs/statistik.md``,
   GitHub Issue bodies render Markdown via
   ``src/utils/text.py:_MARKDOWN_NORMALISE_UNSAFE_RE``. Zero-width
   bytes survive and reach the rendered Markdown verbatim.

Steganographic / prompt-injection surface
=========================================

The same family is also a documented prompt-injection smuggling
primitive. ``\\u2060``-``\\u2064`` are the canonical "invisible
Unicode steganography" alphabet in published research (e.g. Kasperski
2024 "ZWFP", "Sneaky Text" technique published 2023): combinations
of WJ + INVISIBLE TIMES + INVISIBLE SEPARATOR + INVISIBLE PLUS encode
arbitrary bytes that survive copy-paste from a log / RSS feed / GitHub
Issue body into an LLM context window — invisible to the human, fully
visible to the model. ``\\u180e`` adds a sixth bit of payload and
defeats every "strip ZWSP family" filter that relies on the
``\\u200x`` band.

Severity
========
**HIGH** — Trojan-Source-class display confusion AND prompt-injection
primitive. Public artefact (``docs/feed.xml`` /
``docs/feed_health.json`` / ``docs/feed-health.md`` / ``docs/sitemap.xml``)
served from ``https://origamihase.github.io/wien-oepnv/`` is the
attacker's published landing surface; every operator triaging off the
artefact, every RSS subscriber, every LLM training / RAG ingestion
pipeline that scrapes the feed inherits the smuggled payload.

Fix shape
=========
Mirror the canonical 2026-05-11 "Tag-Character / Variation-Selector
Drift" fix shape: extend the always-strip floor
``_INVISIBLE_DANGEROUS_RE`` with the new code-point bands and widen
each of the eight sibling validators in lockstep. The widening is
**additive only against the invisible-character family** — every
pre-fix-covered code point still matches post-fix; legitimate German
(ä/ö/ü/Ä/Ö/Ü/ß), CJK, and emoji content is untouched. The inventory
test ``test_sibling_regex_covers_canonical_invisible_dangerous_set``
in ``tests/test_sentinel_tag_chars_variation_selectors_invisible_drift/``
provides the pinned sibling-superset invariant; the per-band tests
below assert the new code points are matched by every sibling.
"""

# Aufgeteilt am 2026-11-02 (höchstens 700 Zeilen je Datei): Hilfen und Fixtures in
# _support.py, die Tests in test_teil_<n>.py in ihrer alten Reihenfolge.
