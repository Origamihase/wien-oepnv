"""Sentinel PoC: ``allow_nan=False`` writer-defence drift across the
five companion ``data/stations.json`` writers, the cache-sidecar writer,
and the Baustellen coordinate parser.

The 2026-05-14 coordinate-finite/range round (PR #1485, commit
``31570fe``) pinned ``allow_nan=False`` on
:func:`src.places.merge.write_stations` so a future bypass of the
parser-level finite floor at :class:`src.places.hafas_client`
would surface as a loud
:class:`ValueError` at write time rather than silently corrupting the
committed ``data/stations.json`` artefact with non-standard
``NaN`` / ``Infinity`` / ``-Infinity`` JSON literals (invalid per
RFC 8259, rejected by every strict downstream consumer:
``JSON.parse`` in every modern browser, ``serde_json`` in Rust,
``encoding/json`` in Go).

The drift this round closes: **five additional writer sites all write to
the same on-disk artefact (``data/stations.json``) but only the
canonical** ``src.places.merge.write_stations`` **had the writer-defence
pin.** Plus the cache writer (``cache/<provider>/events.json``) which
the Baustellen feed lands coordinates into.

Sites enumerated
================

(A) ``data/stations.json`` writers — committed to ``main`` by the
    weekly ``update-stations.yml`` cron:

      * :func:`scripts.update_station_directory.write_json` — the
        primary OEBB ``Verzeichnis der Verkehrsstationen`` Excel
        writer.  The companion CSV / GTFS / VOR parsers in this
        script (:func:`_coerce_float_value` at line 455) accept
        ``float('nan')`` / ``float('inf')`` because Python's
        ``float()`` constructor accepts the literal strings ``"nan"``,
        ``"inf"``, ``"infinity"`` etc. and the ``isinstance(value,
        int | float)`` shape check on JSON-decoded numeric values
        accepts the non-finite literals that ``json.loads`` parses
        from a compromised upstream JSON.

      * :func:`scripts.enrich_station_aliases.write_stations_payload`
        — the alias-enrichment writer that re-reads ``data/stations.json``
        from the previous cron step and writes it back after appending
        aliases from VOR / GTFS / pendler-alternative-names sources.
        A poisoned coordinate from an upstream step propagates
        verbatim because the alias enricher never touches
        ``latitude`` / ``longitude``.

      * :func:`scripts.update_all_stations.write_stations_payload` —
        the orchestrator that copies ``data/stations.json`` into a
        temp directory, runs every sub-script in sequence against the
        temp file, and atomically copies the result back to
        ``data/stations.json``.  The temp file's writer is the last
        layer before the commit.

      * :func:`scripts.update_wl_stations.merge_into_stations` (line
        1226 pre-fix) — the Wien-Linien OGD CSV merger.  The local
        :func:`scripts.update_wl_stations._coerce_float` parser
        accepts non-finite floats via ``float(text)`` if the upstream
        CSV cell is the literal string ``"nan"`` / ``"inf"`` /
        ``"infinity"`` (per Python's :class:`float` constructor
        contract).

(B) Cache writer (``cache/<provider>/events.json``) — committed to
    ``main`` by the IFTTT-triggered ``update-cycle.yml``:

      * :func:`src.utils.cache.write_cache` — the unified cache
        writer used by every provider.  The Baustellen feed
        (``scripts.update_baustellen_cache``) emits
        ``location.coordinates = {"lat": float, "lon": float}`` into
        each cached event; a compromised Stadt-Wien Baustellen
        upstream replying with ``{"coordinates": [NaN, Infinity]}``
        propagates through the parser and lands as non-standard
        literals in the committed cache file.

(C) Baustellen parser — defence-in-depth at the ingest boundary so
    a poisoned coordinate is *dropped* (rather than crashing the
    writer for the entire batch):

      * :func:`scripts.update_baustellen_cache._build_location`
        (line 509 pre-fix) — performs ``float(coordinates[0])`` /
        ``float(coordinates[1])`` without ``math.isfinite()`` or
        WGS84-range guards.  Mirrors the canonical floor pinned at
        :func:`src.places.hafas_client._extract_first_location`.

Threat model
============

Three distinct attacker positions can plant non-finite or out-of-range
coordinates into the cron pipeline:

  1. **Compromised upstream**: ``data.wien.gv.at`` (Baustellen),
     OEBB ``Verzeichnis der Verkehrsstationen`` (Excel mirror),
     OGD Wien-Linien CSVs, the operator
     mirror of GTFS / VOR.  Any compromised JSON / CSV / XLSX
     response carrying the non-standard ``NaN`` / ``Infinity`` /
     ``-Infinity`` literal flows through the local ``float()`` /
     ``isinstance(..., int | float)`` shape checks without rejection.

  2. **MITM / DNS hijack** on any of the above network paths.

  3. **Poisoned on-disk cache / state file** — the alias-enrichment
     step and the orchestrator both re-read ``data/stations.json``
     from the previous cron run.  A planted non-finite coordinate
     value survives ``json.loads`` (Python's default lenient mode
     parses ``NaN`` / ``Infinity`` literals as ``float('nan')`` /
     ``float('inf')``) and propagates verbatim through the rewrite.

Public sinks impacted
=====================

  * ``data/stations.json`` — committed to ``main`` by
    ``update-stations.yml`` on every weekly cron tick.  Served from
    the GitHub web UI, the ``raw.githubusercontent.com`` mirror,
    the GitHub Pages mirror
    (``https://origamihase.github.io/wien-oepnv/stations.json``).
    Loaded by every downstream consumer that maps Vienna stations to
    their canonical coordinates.

  * ``cache/baustellen_*/events.json`` and the parallel
    ``cache/oebb_*/`` / ``cache/wl_*/`` / ``cache/vor_*/`` events
    files — committed to ``main`` by the IFTTT-triggered
    ``update-cycle.yml``.  Consumed by the feed builder which emits
    the public RSS at ``docs/feed.xml``.

Severity: **MEDIUM** — public-artefact data-integrity attack with
multi-vector attacker positions.  No code execution (the on-disk
values are plain floats), no credential leak, no SSRF.  Same shape
class as the historical Trojan-Source drift rounds: the canonical
floor (here, "finite, WGS84-range coordinate" + ``allow_nan=False``
defence pin) must apply uniformly across every writer site that
lands data into a committed JSON artefact.

The fix
=======

Six coordinated edits, all pinned by this test file (the former
``scripts/fetch_google_places_stations.py`` writers were removed with
that script on 2026-10-16):

  1. ``scripts/update_station_directory.py:write_json`` —
     pass ``allow_nan=False`` to ``json.dump``.
  2. ``scripts/enrich_station_aliases.py:write_stations_payload`` —
     same.
  3. ``scripts/update_all_stations.py:write_stations_payload`` —
     same.
  4. ``scripts/update_wl_stations.py:merge_into_stations`` (the
     ``json.dump`` near line 1226) — same.
  5. ``src/utils/cache.py:write_cache`` — pass ``allow_nan=False``
     to ``json.dump``.
  6. ``scripts/update_baustellen_cache.py:_build_location`` —
     reject non-finite (``math.isfinite``) and out-of-WGS84-range
     coordinates at the parser boundary so the writer-side floor is
     pure defence-in-depth (the legitimate events in the same batch
     still serialise cleanly with their address and metadata).

Inventory invariant
===================

Every committed-to-main JSON writer that may carry float coordinates
must pin ``allow_nan=False`` (writer-side defence-in-depth) AND every
coordinate-emitting parser must apply the canonical finite + WGS84-
range floor at its boundary.  The ``test_inventory_*`` cases below
each grep the function source for the required pin so any future
edit that drops the contract fails the test.
"""

# Aufgeteilt am 2026-11-02 (höchstens 700 Zeilen je Datei): Hilfen und Fixtures in
# _support.py, die Tests in test_teil_<n>.py in ihrer alten Reihenfolge.
