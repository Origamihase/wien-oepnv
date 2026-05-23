"""Sentinel PoC: Trojan-Source / BiDi-Mark leakage at the **two remaining**
``ensure_ascii=False`` JSON writer sites that the 2026-05-23 GeoNetz +
i18n closing round flagged as the open Trojan-Source axis.

Round 13 (PR #1438) closed the canonical library writer
``src/places/merge.py:write_stations`` via the
``scrub_trojan_source_primitives`` helper. Round 14 (the eight-sink
sibling closure in ``test_sentinel_script_station_writers_trojan_source.py``)
extended the scrubber to every named script-level
``ensure_ascii=False`` station-directory writer. The 2026-05-23 round
shipped the size-cap walker
(``tests/test_sentinel_size_cap_audit_walker.py``) AND the non-finite
walker (``tests/test_sentinel_non_finite_literal_audit_walker.py``),
explicitly naming the Trojan-Source axis as the remaining open
closing-rule item::

    Future canonical-loader rounds should ship the walker alongside
    the per-site fix so every parser-site axis (RecursionError +
    size-cap + non-finite-literal + Trojan-Source scrub) is
    programmatically enforced from the start. With this round all
    three of the parser-site canonical axes [...] are now
    programmatically enforced; the Trojan-Source scrub axis remains
    the open closing-rule item for a future round.

Two sibling ``ensure_ascii=False`` writer sinks survived past Round 14
unscrubbed:

1. **``scripts/extract_oebb_geonetz_stops.py:main`` (line ~300)** —
   writes the compact ÖBB GeoNetz projection to
   ``data/oebb_geonetz_stops.json`` via
   ``json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)``
   with NO scrubber on the payload. The payload's ``stops[].name`` /
   ``stops[].address`` / ``stops[].ifopt_id`` / ``stops[].bsts_id`` /
   ``stops[].eva_nr`` fields are taken verbatim from the upstream
   GeoNetz dump (``data.oebb.at`` ÖBB-Infrastruktur AG endpoint). A
   compromised CDN / DNS hijack / MITM on the ÖBB-Infrastruktur fetch
   carrying U+202E in any of those fields lands the bytes in the
   committed ``data/oebb_geonetz_stops.json`` sidecar — the journal
   entry for the 2026-05-23 GeoNetz round already pinned this same
   file's parser-site fix (size-cap + non-finite literal), but the
   writer-side Trojan-Source scrub was missed.

2. **``scripts/apply_station_overrides.py:apply_overrides`` (line ~324)** —
   writes ``data/stations.json`` after applying the curated overrides
   list via
   ``json.dumps(stations_payload, indent=2, ensure_ascii=False, allow_nan=False)``
   with NO scrubber on the resulting payload. Two attack vectors:

   * The loaded ``stations_payload`` may carry a U+202E from a
     previously-poisoned ``data/stations.json`` (planted via a
     bypass of the canonical writer, surviving from a corrupted
     previous cron run, or written by an early-deployment build
     pre-dating the Round 12-14 closing rounds) — the override
     script reads-then-writes the file without retroactive scrub.
   * The ``_op_restore`` handler at line 172 inserts the
     ``entry_template`` from the overrides file verbatim via
     ``dict(entry_template)`` — so a hostile PR landing a tampered
     ``data/stations_overrides.json`` carrying U+202E in the
     ``entry`` ``name`` / ``address`` field plants the BiDi mark
     directly into ``data/stations.json``.

Both files are committed to ``main`` by the weekly
``update-stations.yml`` cron pipeline (which sequences
``update_station_directory.py`` → ``apply_station_overrides.py`` →
``update_all_stations.py``) and rendered via ``cat`` / ``less`` /
``git log -p`` / ``git show`` / the GitHub web UI / IDE preview —
every viewer that honours BiDi reversal displays the
attacker-controlled byte-flip.

Fix shape (identical to Round 13-14)
=====================================

  * Reuse ``src/utils/serialize.py:scrub_trojan_source_primitives``
    (added in Round 12) so the canonical attack-byte union stays
    single-sourced across every operator-facing JSON sidecar writer.
  * Each writer applies the scrubber to the incoming payload BEFORE
    ``json.dumps`` — ingestion-boundary defence so the dangerous bytes
    never reach the serialiser.
  * ``ensure_ascii=False`` is preserved at every writer so legitimate
    German content (umlauts ä/ö/ü/Ä/Ö/Ü + sharp s ß + every other safe
    Unicode code point) stays compact in the weekly commit diff.

The companion ``tests/test_sentinel_trojan_source_audit_walker.py``
is the closing-rule programmatic walker for this axis: every future
``json.dump(..., ensure_ascii=False, ...)`` /
``json.dumps(..., ensure_ascii=False, ...)`` callsite in ``src/`` or
``scripts/`` must call ``scrub_trojan_source_primitives`` in the same
function (or be added to the documented allowlist of legitimate
alternative-defence sites).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# Canonical Trojan-Source / zero-width / Unicode-line-terminator / C1
# terminal-escape primitives — byte-exact mirror of the set pinned in
# ``tests/test_sentinel_script_station_writers_trojan_source.py``
# (Round 14) and ``tests/test_sentinel_places_stations_trojan_source.py``
# (Round 13) so any future widening of the canonical floor is enforced
# uniformly across the committed-sidecar writer family.
_TROJAN_SOURCE_PRIMITIVES = (
    # BiDi formatting controls (CVE-2021-42574 first half).
    "‪",  # LRE Left-To-Right Embedding
    "‫",  # RLE Right-To-Left Embedding
    "‬",  # PDF Pop Directional Formatting
    "‭",  # LRO Left-To-Right Override
    "‮",  # RLO Right-To-Left Override
    # BiDi isolates (CVE-2021-42574 second half).
    "⁦",  # LRI Left-To-Right Isolate
    "⁧",  # RLI Right-To-Left Isolate
    "⁨",  # FSI First Strong Isolate
    "⁩",  # PDI Pop Directional Isolate
    # Zero-width / left-right marks.
    "​",  # ZWSP
    "‌",  # ZWNJ
    "‍",  # ZWJ
    "‎",  # LRM
    "‏",  # RLM
    "؜",  # ALM
    # Unicode line / paragraph separators (SIEM splitter primitive).
    " ",  # LINE SEPARATOR
    " ",  # PARAGRAPH SEPARATOR
    # Byte Order Mark / ZWNBSP.
    "﻿",
    # C1 terminal escape primitives (8-bit colour/SGR start, OSC, DCS).
    "\x9b",  # CSI
    "\x9d",  # OSC
    "\x90",  # DCS
)

# Mapping from each primitive to its raw UTF-8 byte sequence.
# Pre-fix every byte sequence appears in the on-disk file verbatim
# (ensure_ascii=False emits each as its raw UTF-8 form);
# post-fix none of them do (the scrubber strips at the ingestion
# boundary).
_UTF8_BYTES = {p: p.encode("utf-8") for p in _TROJAN_SOURCE_PRIMITIVES}

# Smoking-gun primitive: U+202E RLO. The 3-byte UTF-8 sequence
# E2 80 AE drives the actual BiDi reversal that hides the attack
# in any operator review pass.
_RLO_UTF8 = b"\xe2\x80\xae"


# ============================================================================
# Sink 1: ``scripts/extract_oebb_geonetz_stops.py`` — committed
# ``data/oebb_geonetz_stops.json`` via ``json.dumps(payload,
# ensure_ascii=False, indent=2, allow_nan=False)``
# ============================================================================


def _planted_geonetz_feature_collection(planted_value: str) -> dict[str, Any]:
    """Build a minimal GeoNetz FeatureCollection carrying *planted_value*
    in every operator-facing string field that flows into
    ``data/oebb_geonetz_stops.json``."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [16.37, 48.21]},
                "properties": {
                    "STP_ID": f"STP{planted_value}001",
                    "BSTS_ID": f"bsts{planted_value}001",
                    "STP_NAME": f"Hauptbahnhof{planted_value}moc.live",
                    "STP_LAT": 48.21,
                    "STP_LON": 16.37,
                    "EVA_NR": f"81{planted_value}001",
                    "IFOPT_ID": f"at:1:{planted_value}:01",
                    "STP_ROADNAME": f"Bahnhof{planted_value}straße 1",
                    "STP_FROMDATE": "2024-12-15T00:00:00",
                    "STP_TODATE": "2025-12-14T23:59:59",
                },
            }
        ],
    }


def _write_raw_geonetz(tmp_path: Path, payload: dict[str, Any]) -> Path:
    raw_path = tmp_path / "raw_geonetz.json"
    raw_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return raw_path


def _run_extract_main(tmp_path: Path, raw_path: Path) -> Path:
    """Invoke ``scripts/extract_oebb_geonetz_stops.main`` end-to-end and
    return the produced output path."""
    from scripts.extract_oebb_geonetz_stops import main as extract_main

    output_path = tmp_path / "oebb_geonetz_stops.json"
    rc = extract_main([
        "--raw", str(raw_path),
        "--output", str(output_path),
        "--source-url", "https://example.invalid/raw.zip",
    ])
    assert rc == 0, f"extract main returned non-zero: {rc}"
    return output_path


def test_extract_geonetz_no_rlo_leak(tmp_path: Path) -> None:
    """A planted ``STP_NAME`` carrying U+202E reaches
    ``data/oebb_geonetz_stops.json`` pre-fix via the
    ``json.dumps(payload, ensure_ascii=False, ...)`` writer."""
    raw_path = _write_raw_geonetz(
        tmp_path, _planted_geonetz_feature_collection("‮")
    )
    output_path = _run_extract_main(tmp_path, raw_path)

    raw_out = output_path.read_bytes()
    assert _RLO_UTF8 not in raw_out, (
        "U+202E (RLO) leaked verbatim into data/oebb_geonetz_stops.json "
        "via the extract main writer — BiDi reversal is now active for "
        "any cat / less / git log -p / GitHub web UI / IDE viewer of "
        "the committed sidecar."
    )


@pytest.mark.parametrize("primitive", _TROJAN_SOURCE_PRIMITIVES)
def test_extract_geonetz_strips_every_primitive(
    tmp_path: Path, primitive: str
) -> None:
    """Every canonical attack-byte primitive must be rejected at the
    ingestion boundary — a single primitive surviving in raw form
    re-opens the attack via a future GeoNetz-controlled field."""
    raw_path = _write_raw_geonetz(
        tmp_path, _planted_geonetz_feature_collection(primitive)
    )
    output_path = _run_extract_main(tmp_path, raw_path)

    raw_out = output_path.read_bytes()
    expected = _UTF8_BYTES[primitive]
    assert expected not in raw_out, (
        f"Trojan-Source primitive U+{ord(primitive):04X} leaked into "
        f"data/oebb_geonetz_stops.json as raw UTF-8 bytes ({expected!r}) "
        "via scripts/extract_oebb_geonetz_stops.main."
    )


def test_extract_geonetz_preserves_german_umlauts(tmp_path: Path) -> None:
    """Legitimate German content (ä/ö/ü/Ä/Ö/Ü/ß) must survive the
    scrubber so the weekly commit diff stays compact."""
    feature_collection = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [16.37, 48.21]},
                "properties": {
                    "STP_ID": "STP001",
                    "BSTS_ID": "bsts001",
                    "STP_NAME": "Wien Hütteldorf",
                    "STP_LAT": 48.21,
                    "STP_LON": 16.37,
                    "EVA_NR": "8100001",
                    "IFOPT_ID": "at:1:1:01",
                    "STP_ROADNAME": "Floridsdorfer Brücke",
                    "STP_FROMDATE": "2024-12-15T00:00:00",
                    "STP_TODATE": "2025-12-14T23:59:59",
                },
            }
        ],
    }
    raw_path = _write_raw_geonetz(tmp_path, feature_collection)
    output_path = _run_extract_main(tmp_path, raw_path)

    text = output_path.read_text(encoding="utf-8")
    assert "Hütteldorf" in text
    assert "Floridsdorfer Brücke" in text
    # Sanity: the bloated \u00XX escape form must NOT appear — that
    # would mean we accidentally switched to ensure_ascii=True and
    # ballooned the diff size 4-6x.
    assert "\\u00fc" not in text
    assert "\\u00fc" not in text


# ============================================================================
# Sink 2: ``scripts/apply_station_overrides.py`` — committed
# ``data/stations.json`` via ``json.dumps(stations_payload,
# indent=2, ensure_ascii=False, allow_nan=False)``
# ============================================================================


def _write_planted_stations(tmp_path: Path, planted_value: str) -> Path:
    """Write a stations.json file with U+XXXX-carrying string fields
    on a baseline entry. Simulates a previously-poisoned stations.json
    that survived an earlier bypass and lands in the override re-write."""
    payload = {
        "stations": [
            {
                "name": f"Westbahnhof{planted_value}moc.live",
                "wl_diva": "60201080",
                "latitude": 48.196,
                "longitude": 16.337,
                "aliases": [f"alias{planted_value}evil"],
            }
        ]
    }
    target = tmp_path / "stations.json"
    target.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return target


def _write_overrides(tmp_path: Path) -> Path:
    """Write a minimal overrides file that targets the planted entry
    with ``patch_coords`` (a benign no-op when the coords already
    match). The override file itself is benign — the leak is from
    the loaded payload carrying historic Trojan-Source bytes."""
    overrides = {
        "overrides": [
            {
                "op": "patch_coords",
                "wl_diva": "60201080",
                "latitude": 48.196,
                "longitude": 16.337,
                "reason": "fixture",
                "expires_when": "fixture",
            }
        ]
    }
    target = tmp_path / "stations_overrides.json"
    target.write_text(
        json.dumps(overrides, ensure_ascii=False), encoding="utf-8"
    )
    return target


def _run_apply_overrides(tmp_path: Path, planted_value: str) -> Path:
    """Invoke ``scripts/apply_station_overrides.apply_overrides`` and
    return the rewritten stations.json path."""
    from scripts.apply_station_overrides import apply_overrides

    stations_path = _write_planted_stations(tmp_path, planted_value)
    overrides_path = _write_overrides(tmp_path)
    rc = apply_overrides(stations_path, overrides_path)
    assert rc == 0, f"apply_overrides returned non-zero: {rc}"
    return stations_path


def test_apply_overrides_no_rlo_leak(tmp_path: Path) -> None:
    """A previously-poisoned ``data/stations.json`` entry carrying
    U+202E in its ``name`` survives the override application pre-fix —
    the re-write via ``json.dumps(..., ensure_ascii=False)`` emits the
    BiDi-reversal trigger verbatim."""
    stations_path = _run_apply_overrides(tmp_path, "‮")
    raw_out = stations_path.read_bytes()
    assert _RLO_UTF8 not in raw_out, (
        "U+202E (RLO) survived apply_overrides — the planted byte in "
        "the loaded stations.json was re-emitted verbatim into the "
        "committed file. ``apply_station_overrides.py`` is the missed "
        "scrubber site between update_station_directory and "
        "update_all_stations on the weekly cron."
    )


@pytest.mark.parametrize("primitive", _TROJAN_SOURCE_PRIMITIVES)
def test_apply_overrides_strips_every_primitive(
    tmp_path: Path, primitive: str
) -> None:
    """Every canonical attack-byte primitive must be stripped at the
    apply-overrides re-write — historic poisoned ``data/stations.json``
    bytes are NOT a justifiable propagation source."""
    stations_path = _run_apply_overrides(tmp_path, primitive)
    raw_out = stations_path.read_bytes()
    expected = _UTF8_BYTES[primitive]
    assert expected not in raw_out, (
        f"Trojan-Source primitive U+{ord(primitive):04X} leaked into "
        f"data/stations.json as raw UTF-8 bytes ({expected!r}) via "
        "scripts/apply_station_overrides.apply_overrides."
    )


def test_apply_overrides_preserves_german_umlauts(tmp_path: Path) -> None:
    """Legitimate German content (ä/ö/ü/Ä/Ö/Ü/ß) must survive the
    re-write so the weekly commit diff stays compact."""
    payload = {
        "stations": [
            {
                "name": "Wien Hütteldorf",
                "wl_diva": "60201080",
                "latitude": 48.196,
                "longitude": 16.337,
                "aliases": ["Hauptbahnhof", "Floridsdorfer Brücke", "Praterstraße"],
            }
        ]
    }
    stations_path = tmp_path / "stations.json"
    stations_path.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    overrides_path = _write_overrides(tmp_path)

    from scripts.apply_station_overrides import apply_overrides

    rc = apply_overrides(stations_path, overrides_path)
    assert rc == 0

    text = stations_path.read_text(encoding="utf-8")
    assert "Hütteldorf" in text
    assert "Floridsdorfer Brücke" in text
    assert "Praterstraße" in text
    # Sanity: the bloated \u00XX escape form must NOT appear — that
    # would mean we accidentally switched to ensure_ascii=True and
    # ballooned the diff size 4-6x.
    assert "\\u00fc" not in text
    assert "\\u00df" not in text


# ============================================================================
# AST static-invariant: the scrub call is in the function bodies
# ============================================================================


def _function_calls_scrub(
    module_path: Path, function_name: str
) -> bool:
    """Return ``True`` iff the function named *function_name* in
    *module_path* contains a call to ``scrub_trojan_source_primitives``
    in its body (excluding docstrings).

    Defence-in-depth against a future refactor that drops the
    scrubber in either fix site: the byte-level PoC tests above
    pin the runtime behaviour; this static check pins the SHAPE
    so a future maintainer who replaces the scrub with an
    out-of-band defence (or removes it entirely) fails the test
    at PR-review time regardless of whether the runtime defence
    fortuitously still passes (e.g. via a different sibling
    helper)."""
    import ast

    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != function_name:
            continue
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Name)
                and sub.func.id == "scrub_trojan_source_primitives"
            ):
                return True
    return False


def test_extract_main_calls_scrub() -> None:
    """``scripts/extract_oebb_geonetz_stops.main`` MUST call
    ``scrub_trojan_source_primitives`` on the payload before the
    ``json.dumps(..., ensure_ascii=False, ...)`` writer."""
    path = REPO_ROOT / "scripts" / "extract_oebb_geonetz_stops.py"
    assert _function_calls_scrub(path, "main"), (
        "scripts/extract_oebb_geonetz_stops.py:main must call "
        "scrub_trojan_source_primitives before json.dumps — the only "
        "ingestion-boundary defence between a poisoned upstream "
        "GeoNetz dump and the committed data/oebb_geonetz_stops.json "
        "sidecar."
    )


def test_apply_overrides_calls_scrub() -> None:
    """``scripts/apply_station_overrides.apply_overrides`` MUST call
    ``scrub_trojan_source_primitives`` on the stations_payload before
    the ``json.dumps(..., ensure_ascii=False, ...)`` re-write."""
    path = REPO_ROOT / "scripts" / "apply_station_overrides.py"
    assert _function_calls_scrub(path, "apply_overrides"), (
        "scripts/apply_station_overrides.py:apply_overrides must call "
        "scrub_trojan_source_primitives before json.dumps — the only "
        "ingestion-boundary defence between a previously-poisoned "
        "stations.json (or a tampered overrides entry) and the "
        "re-committed data/stations.json sidecar."
    )
