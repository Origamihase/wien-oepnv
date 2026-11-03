"""Sentinel PoC: non-finite literal defence drift across the **network-
tainted** / **env-tainted** / **state-sidecar** JSON parser landscape —
the symmetric companion to the on-disk reader closure of PR #1503.

PR #1503 closed the reader-side gap for the eleven committed-state-file
readers (``read_capped_json`` + 10 callers).  Every ``json.loads(...)``
call inside those readers now pins ``parse_constant`` +
``parse_float`` hooks that reject the non-finite literal family
(``NaN`` / ``Infinity`` / ``-Infinity`` / ``1e1000``-scientific-
overflow).  The drift this round closes:

* **15 network-tainted / env-tainted / state-sidecar JSON parsers**
  outside the Round-1503 inventory were left at lenient-mode
  ``json.loads(content)`` / ``response.json()`` — every one is the
  symmetric companion to the writer-pin family for a DIFFERENT taint
  channel.

Threat model
============

The Round-1503 channel was *committed on-disk state files*
(``data/first_seen.json``, ``data/stations.json``,
``cache/<provider>/events.json``, etc.).  The channel this round
closes is everything that PR #1503 did NOT enumerate:

1. **Network-tainted JSON (11 sites)** — every HTTP response parsed
   into the build pipeline:

   * Wiener Linien (``src/providers/wl_fetch.py:_get_json``)
   * HAFAS (``src/places/hafas_client.py``)
   * OSM Overpass (``src/places/osm_client.py``)
   * GitHub API issue-submit (``src/feed/reporting.py`` error branch
     + success branch)
   * Overpass smoke probe (``scripts/check_overpass_status.py``)
   * VOR access-id verifier (``scripts/verify_vor_access_id.py``)
   * Wien Baustellen (``scripts/update_baustellen_cache.py``)
   * VAO ``/departureBoard`` Hbf (``scripts/update_stammstrecke_hbf.py``)
   * VAO ``/trip`` legacy (``scripts/update_stammstrecke_status.py``)
   * VAO error envelope (``scripts/update_stammstrecke_status.py``)

   A compromised upstream / DNS-hijack / MITM ships a body containing
   ``NaN`` / ``Infinity`` / ``-Infinity`` / ``1e1000`` literals.  The
   lenient-mode ``response.json()`` / ``json.loads(content)`` returns
   a Python structure with ``float('nan')`` / ``float('inf')`` inside.
   The non-finite float then poisons:

   - **Comparisons** (``nan != nan`` is True — breaks every dedup
     invariant and timestamp ordering check).
   - **Arithmetic** (``nan + x`` is nan — silently corrupts latency
     averages, retention windows, delay calculations).
   - **Round-trip back to the writer** — the writer-pin
     (``allow_nan=False`` from Round 1485/1487/1488/1491) catches the
     non-finite at serialisation time and raises ``ValueError``,
     crashing the cron pipeline mid-write with no recovery.

2. **Env-tainted JSON (1 site)** — the ``BOUNDINGBOX_VIENNA``
   override:

   * ``scripts/update_station_directory.py:_parse_bounding_box``

   A leaked CI env / compromised secret store / hostile operator
   plants ``{"min_lat": NaN, ...}`` into the env variable.  The
   downstream ``float(data["min_lat"])`` coercion lifts NaN into the
   ``BoundingBox`` dataclass — every place-fetch URL constructed from
   the bounds carries the non-finite, and the writer-pin round-trip
   crashes the next persist.

3. **Disk-tainted state sidecars missed by Round 1503 (3 sites)** —
   the on-disk reader closure named the canonical helpers but did
   NOT enumerate three sibling readers that share the same threat
   model:

   * ``src/feed_build/state.py:_read_state_capped`` — the second
     ``data/first_seen.json`` reader, called from ``_save_state``
     to merge with existing state.  Same file as the Round-1503
     ``_load_state`` reader but a separate function.
   * ``scripts/update_stammstrecke_status.py:_load_pending_trips``
     (``cache/stammstrecke/pending_trips.json``)
   * ``scripts/update_stammstrecke_status.py:_load_recently_finalised``
     (``cache/stammstrecke/recently_finalised.json``)

The fix shape mirrors PR #1503 exactly: every parser site pins the
two canonical hooks (``_reject_non_finite_constant`` +
``_reject_non_finite_float`` from ``src/utils/files.py``), either
directly as kwargs or via the new ``loads_finite()`` wrapper.

Impact symmetry
===============

The post-fix behaviour is identical to PR #1503 — both hooks raise
``json.JSONDecodeError`` (a ``ValueError`` subclass), which every
network/env/disk parser's existing
``except (ValueError, RecursionError, json.JSONDecodeError)`` handler
catches transparently.  No per-callsite ``except`` widening was
needed.

Coverage
========

* 5 canonical-helper behavioural tests for the new
  ``loads_finite()`` wrapper and the two existing hooks.
* 15 inventory pins (source-grep) — one per enumerated site.
* Multiple per-site behavioural PoCs covering both ``NaN`` /
  ``Infinity`` literal tokens AND ``1e1000`` scientific-notation
  overflow, plus a finite-round-trip regression guard.

Marker: SENTINEL_NETWORK_TAINTED_NON_FINITE_DRIFT.
"""

# Aufgeteilt am 2026-11-02 (höchstens 700 Zeilen je Datei): Hilfen und Fixtures in
# _support.py, die Tests in test_teil_<n>.py in ihrer alten Reihenfolge.
