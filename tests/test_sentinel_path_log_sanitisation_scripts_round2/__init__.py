"""Sentinel PoC: Round-2 sibling drift of the 2026-05-11
``read_capped_json``/``read_capped_text`` path-log sanitisation fix in the
operator-facing ``scripts/`` directory.

The 2026-05-11 Caller-Drift round (PR closing the
``stations.py:_read_capped_json`` clone + the five sibling WARNING sinks in
``src/utils/env.py`` and ``src/build_feed.py``) explicitly named-but-deferred
the inverse grep across ``scripts/``::

    "the inverse grep across ``scripts/`` (currently ~14 sibling sites) is
     named-but-deferred to a Round 2 that does not block the canonical
     drift closure shipped here."

This file is that Round-2 closure. The grep enumerates 25 caller-side
WARNING/INFO sinks across three CLI-driven scripts that interpolate the
operator-controlled ``path`` argument into the bare ``%s`` format spec:

  * ``scripts/enrich_station_aliases.py`` (6 sites):
      - ``_load_vor_names``                    L310 (file-not-found WARNING)
      - ``_load_vor_mapping``                  L337 (file-not-found WARNING)
                                               L349 (could-not-parse WARNING)
      - ``_load_gtfs_index``                   L388 (file-not-found WARNING)
      - ``_load_pendler_alternative_names``    L425 (file-not-found INFO)
                                               L434 (invalid-JSON WARNING)
  * ``scripts/update_station_directory.py`` (17 sites):
      - ``_load_gtfs_locations``               L442 (file-not-found WARNING)
                                               L478 (csv.Error WARNING)
      - ``_load_wienerlinien_locations``       L488 (file-not-found WARNING)
                                               L516 (csv.Error WARNING)
      - ``_load_vor_locations``                L531 (file-not-found WARNING)
                                               L561 (csv.Error WARNING)
      - ``_load_existing_station_entries``     L607 (parse-failure WARNING)
      - ``load_vor_stops``                     L1086 (FNF INFO)
                                               L1089 (csv.Error WARNING)
                                               L1116 (no-rows INFO)
                                               L1118 (load-summary INFO)
      - ``load_pendler_station_ids``           L1756 (file-not-found WARNING)
      - ``load_pendler_name_candidates``       L1807 (file-not-found INFO)
                                               L1821 (parse-failure WARNING)
                                               L1827 (shape-error WARNING)
                                               L1832 (shape-error WARNING)
      - ``write_json``                         L1878 (write-summary INFO)
  * ``scripts/update_wl_stations.py`` (2 sites):
      - ``load_vor_mapping``                   L539 (file-not-found INFO)
                                               L549 (parse-failure WARNING)

Threat model
============
Every path in these sites is operator-controlled (CLI ``--vor-stops``,
``--vor-mapping``, ``--gtfs-stops``, ``--wl-haltepunkte``,
``--pendler``, ``--pendler-candidates``, ``--output``, ``--stations``,
``--haltepunkte``, ``--haltestellen``). The scripts run via cron under
the orchestrator (``update_all_stations.py`` invokes them with
``subprocess.run(check=True)``) so a hostile config / PR / env-var
override pointing at a directory whose path string carries Trojan-Source
primitives lands those primitives into:

  * The script's stderr (captured by the orchestrator and the cron
    diagnostic dump).
  * Any operator-facing log file that aggregates the script output.
  * Pytest's ``caplog`` capture (which exposes ``record.args[0]``
    BEFORE the :class:`SafeFormatter` runs — a third-party log handler
    or custom plugin sees the raw bytes).
  * Any downstream consumer that reads ``record.msg`` /
    ``record.getMessage()`` from the propagated record before formatter
    sanitisation (rsyslog with Python logging adapters, structured log
    JSON emitters that don't route through :class:`SafeJSONFormatter`).

The Trojan-Source primitive set (canonical, mirrors the prior round):

  * ``‮``   U+202E RIGHT-TO-LEFT OVERRIDE — visually reverses subsequent
            text in any Unicode-aware terminal, phishing primitive
            (CVE-2021-42574).
  * ``​``  U+200B ZERO WIDTH SPACE — invisible cache-key / equality
            poisoning primitive.
  * ``\x9b``  U+009B 8-bit CSI — survives the 7-bit ``_ANSI_ESCAPE_RE``
              defence, triggers SGR colour interpretation on every
              8-bit-C1-honouring terminal (xterm with eightBitInput,
              several BSD consoles, rxvt in 8-bit mode).
  * ``\x9d``  U+009D 8-bit OSC — companion to CSI; same defence bypass.
  * ``\x1b``  U+001B ESC — ANSI prefix, terminal-escape primitive.
  * ``\x07``  U+0007 BEL — terminal-bell denial-of-attention.
  * ``\n``    newline (record terminator) — log-record forgery in any
              line-based consumer (SIEM splitters, rsyslog, journald,
              Promtail).
  * ``\r``    carriage return — log overwrite primitive (terminal
              redraws over the prior line; the operator never sees
              the original WARNING).
  * ``\U000e0020``  U+E0020 Unicode Tag SPACE — invisible-instruction
                    smuggling primitive (2024 OpenAI disclosure).
  * ``︀``    U+FE00 VARIATION SELECTOR-1 — 4-bit-payload
              steganography.

Defence shape
=============
Each fixed site adopts the canonical ``_path_fingerprint`` shape pinned
in :func:`src.utils.env._path_fingerprint`::

    hashlib.sha256(str(path).encode("utf-8", errors="replace")).hexdigest()[:12]

This is:
  * A CodeQL-recognised barrier (``hashlib`` is a documented sanitiser
    sink for ``py/clear-text-logging-sensitive-data``).
  * Trojan-Source-clean — hex-only ``[0-9a-f]`` output.
  * Operator-correlatable — running ``sha256(str(path))[:12]`` locally
    on a candidate path confirms identity for cron-pipeline diagnosis.
  * Stable across runs for a given path — useful for log aggregation
    and SIEM grouping.

Each script gains:
  1. A module-level ``import hashlib``.
  2. A module-level ``_path_fingerprint`` helper mirroring
     :func:`src.utils.env._path_fingerprint`.
  3. Every operator-controlled-path WARNING / INFO log line is updated
     to use ``[path-sha256=%s]`` with ``_path_fingerprint(path)`` in
     place of the raw ``path`` argument.
"""

# Aufgeteilt am 2026-11-02 (höchstens 700 Zeilen je Datei): Hilfen und Fixtures in
# _support.py, die Tests in test_teil_<n>.py in ihrer alten Reihenfolge.
