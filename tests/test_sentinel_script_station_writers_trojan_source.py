"""Sentinel PoC: Trojan-Source / BiDi-Mark leakage at the **script-level**
station-directory writers that the *BiDi-Mark Drift Round 13*
closing-checklist named but deferred.

Round 13 (PR #1438) closed the canonical library function
``src/places/merge.py:write_stations`` / ``load_stations`` via the
``scrub_trojan_source_primitives`` helper added in Round 12
(``src/utils/serialize.py``). The Round-13 closing checklist explicitly
named eight sibling script-level writers that bypass the canonical
library function and write ``data/stations.json`` (or sibling
``data/vor-haltestellen.mapping.json``) via direct
``json.dump(..., ensure_ascii=False, ...)`` / ``json.dumps(...,
ensure_ascii=False, ...)`` calls. Each of these is reached from the
weekly ``update-stations.yml`` cron pipeline (via
``scripts/update_all_stations.py``) which commits ``data/`` to ``main``
with ``add_options: "-A"`` — exactly the same threat surface Rounds
10-13 closed at the library level.

Sinks pinned in this PoC
=========================

Sinks 1-2 (``scripts/fetch_google_places_stations.py:_write_if_changed``
and ``_dump_changes``) were removed together with that script on
2026-10-16 (Google Places stage dropped); the numbering below is kept
stable so the section headers still match the Round-13 checklist.

3. ``scripts/update_all_stations.py`` — writes ``{"stations": [...]}``
   to the orchestrator's temp file which is then copy-back'd to
   ``data/stations.json`` (this is the file the weekly cron commits).
4. ``scripts/enrich_station_aliases.py`` — writes ``data/stations.json``
   after alias enrichment. Both scripts write through
   ``src/places/merge.py:write_stations_payload`` (one function since the
   code audit of 2026-10-31; before, each script carried its own
   ``_write_stations_payload`` copy), so the scrubber runs uniformly at the
   ingestion boundary.
5. ``scripts/update_station_directory.py:write_json`` (line ~1762) —
   writes ``{"stations": [...]}`` to ``data/stations.json`` after the
   OEBB ``Verzeichnis der Verkehrsstationen`` Excel extraction.
6. ``scripts/update_vor_stations.py:merge_into_stations``
   (line ~1208) — writes ``{"stations": [...]}`` to
   ``data/stations.json`` after the VOR merge.
7. ``scripts/update_wl_stations.py:merge_into_stations``
   (line ~734) — writes ``{"stations": [...]}`` to
   ``data/stations.json`` after the WL OGD merge.
8. ``scripts/fetch_vor_haltestellen.py`` — extracts the inline
   ``json.dumps`` write into a new ``_write_mapping_payload`` helper
   so the scrubber can run uniformly at the ingestion boundary
   (writes ``data/vor-haltestellen.mapping.json`` after the VOR
   station-name resolution).

Threat model (identical to Round 13)
=====================================

1. Attacker compromises an upstream provider (OSM Overpass cache poisoning, OEBB ``Verzeichnis der
   Verkehrsstationen`` Excel response tampering, Wien OGD CSV
   poisoning, VAO ReST station-resolution endpoint hijack, leaked CI
   secret store) or an operator mis-edits ``data/stations.json`` /
   ``data/vor-haltestellen.mapping.json`` directly.
2. A planted station entry carrying U+202E (RIGHT-TO-LEFT OVERRIDE)
   in a ``name`` / ``aliases[]`` / ``_formatted_address`` /
   ``station_name`` / ``resolved_name`` field — e.g.
   ``name="Hauptbahnhof<U+202E>moc.live"`` displays as
   ``Hauptbahnhofevil.com`` — reaches one of the script-level
   writers above.
3. Pre-fix each writer serialised the item via
   ``json.dump(..., ensure_ascii=False, ...)`` or
   ``json.dumps(..., ensure_ascii=False, ...)``. ``ensure_ascii=False``
   emits U+202E as its raw UTF-8 byte triplet ``\\xe2\\x80\\xae``, so
   the on-disk file carries the BiDi-reversal trigger.
4. The ``update-stations.yml`` workflow commits the poisoned file to ``main``. The malicious name
   is now visible in ``git log -p`` / ``git show`` / the GitHub web UI
   / ``cat`` / ``less`` / IDE preview — every viewer honours BiDi
   reversal.
5. Operator reviewing the weekly commit misreads the BiDi-reversed
   display as the inverse of the actual planted bytes. The attack
   hides in the operator's own review pass.

Fix shape (identical to Round 13)
==================================

  * Reuse ``src/utils/serialize.py:scrub_trojan_source_primitives``
    (added in Round 12) so the canonical attack-byte union stays
    single-sourced across every operator-facing JSON sidecar writer in
    the codebase.
  * Each writer applies the scrubber to the incoming payload BEFORE
    ``json.dump`` / ``json.dumps`` — ingestion-boundary defence so the
    dangerous bytes never reach the serialiser.
  * ``ensure_ascii=False`` is preserved at every writer so legitimate
    German station names (umlauts ä/ö/ü/Ä/Ö/Ü + sharp s ß + every other
    safe Unicode code point) stay compact in the weekly commit diff.
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
# ``tests/test_sentinel_places_stations_trojan_source.py`` (Round 13)
# and ``tests/test_sentinel_cache_events_trojan_source.py`` (Round 12)
# so any future widening of the canonical floor is enforced uniformly
# across the committed-sidecar writer family.
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


# ---------------------------------------------------------------------
# Sinks 3 and 4: ``src/places/merge.py:write_stations_payload`` — the
# writer of ``scripts/update_all_stations.py`` (orchestrator temp file,
# copied back to ``data/stations.json``) and of
# ``scripts/enrich_station_aliases.py``; one function since the code
# audit of 2026-10-31, before it each script carried its own copy.
# ---------------------------------------------------------------------


def test_write_stations_payload_no_rlo_leak(tmp_path: Path) -> None:
    """The weekly ``update-stations.yml`` cron copies the written file
    back to ``data/stations.json`` and commits it to ``main``."""
    from src.places.merge import write_stations_payload

    target = tmp_path / "stations.json"
    stations = [
        {
            "name": "Westbahnhof‮moc.live",
            "bst_id": "12345",
            "latitude": 48.18,
            "longitude": 16.38,
        }
    ]
    write_stations_payload(target, stations)

    raw = target.read_bytes()
    assert _RLO_UTF8 not in raw, (
        "U+202E leaked via write_stations_payload — the orchestrator's "
        "temp file gets copy-back'd to data/stations.json and committed."
    )


@pytest.mark.parametrize("primitive", _TROJAN_SOURCE_PRIMITIVES)
def test_write_stations_payload_strips_every_primitive(
    tmp_path: Path, primitive: str
) -> None:
    from src.places.merge import write_stations_payload

    target = tmp_path / f"stations-{ord(primitive):04x}.json"
    stations = [
        {
            "name": f"evil{primitive}.example",
            "bst_id": "12345",
            "aliases": [f"alias{primitive}evil"],
            "latitude": 48.18,
            "longitude": 16.38,
        }
    ]
    write_stations_payload(target, stations)

    raw = target.read_bytes()
    expected = _UTF8_BYTES[primitive]
    assert expected not in raw, (
        f"Trojan-Source primitive U+{ord(primitive):04X} leaked via "
        f"write_stations_payload as raw UTF-8 bytes ({expected!r})."
    )


def test_write_stations_payload_preserves_umlauts(tmp_path: Path) -> None:
    from src.places.merge import write_stations_payload

    target = tmp_path / "stations.json"
    stations = [
        {"name": "Wien Hütteldorf", "aliases": ["Floridsdorfer Brücke"], "bst_id": "1"}
    ]
    write_stations_payload(target, stations)

    text = target.read_text(encoding="utf-8")
    assert "Hütteldorf" in text
    assert "\\u00fc" not in text


def test_scripts_bind_the_shared_writer() -> None:
    """Both scripts write through the shared function, so the sinks above are theirs."""
    import importlib

    from src.places.merge import write_stations_payload

    for name in ("scripts.update_all_stations", "scripts.enrich_station_aliases"):
        assert importlib.import_module(name).write_stations_payload is write_stations_payload


# ---------------------------------------------------------------------
# Sink 5: ``scripts/update_station_directory.py:write_json``
# ---------------------------------------------------------------------


def test_update_station_directory_write_json_no_rlo_leak(tmp_path: Path) -> None:
    """The OEBB Excel-extraction writer feeds the canonical
    ``data/stations.json``. A poisoned upstream Excel response (e.g.
    via DNS hijack of the OEBB OGD portal) is the primary delivery
    vector."""
    from scripts.update_station_directory import write_json

    target = tmp_path / "stations.json"
    stations_list = [
        {
            "name": "Wien Hauptbahnhof‮moc.live",
            "bst_id": "100",
            "bst_code": "WHb",
            "in_vienna": True,
        }
    ]
    write_json(stations_list, target)

    raw = target.read_bytes()
    assert _RLO_UTF8 not in raw, (
        "U+202E leaked via update_station_directory.write_json."
    )


@pytest.mark.parametrize("primitive", _TROJAN_SOURCE_PRIMITIVES)
def test_update_station_directory_write_json_strips_every_primitive(
    tmp_path: Path, primitive: str
) -> None:
    from scripts.update_station_directory import write_json

    target = tmp_path / f"stations-{ord(primitive):04x}.json"
    stations_list: list[dict[str, object]] = [
        {
            "name": f"evil{primitive}.example",
            "bst_id": "100",
            "bst_code": "WHb",
        }
    ]
    write_json(stations_list, target)

    raw = target.read_bytes()
    expected = _UTF8_BYTES[primitive]
    assert expected not in raw, (
        f"Trojan-Source primitive U+{ord(primitive):04X} leaked via "
        f"update_station_directory.write_json as raw UTF-8 bytes ({expected!r})."
    )


def test_update_station_directory_write_json_preserves_umlauts(tmp_path: Path) -> None:
    from scripts.update_station_directory import write_json

    target = tmp_path / "stations.json"
    stations_list: list[dict[str, object]] = [{"name": "Wien Hütteldorf", "bst_id": "1"}]
    write_json(stations_list, target)

    text = target.read_text(encoding="utf-8")
    assert "Hütteldorf" in text
    assert "\\u00fc" not in text


# ---------------------------------------------------------------------
# Sink 7: ``scripts/update_wl_stations.py:merge_into_stations``
# ---------------------------------------------------------------------


def test_update_wl_merge_into_stations_no_rlo_leak(tmp_path: Path) -> None:
    """Integration test: a planted WL entry reaches the on-disk
    ``data/stations.json`` via the Wien OGD CSV merge path."""
    from scripts.update_wl_stations import merge_into_stations

    target = tmp_path / "stations.json"
    target.write_text(json.dumps({"stations": []}), encoding="utf-8")
    wl_entries: list[dict[str, Any]] = [
        {
            "name": "Karlsplatz‮moc.live",
            "bst_id": "9000",
            "aliases": ["U-Bahn‮.evil"],
            "latitude": 48.20,
            "longitude": 16.37,
        }
    ]
    merge_into_stations(target, wl_entries)

    raw = target.read_bytes()
    assert _RLO_UTF8 not in raw, (
        "U+202E leaked via update_wl_stations.merge_into_stations."
    )


@pytest.mark.parametrize("primitive", _TROJAN_SOURCE_PRIMITIVES)
def test_update_wl_merge_into_stations_strips_every_primitive(
    tmp_path: Path, primitive: str
) -> None:
    from scripts.update_wl_stations import merge_into_stations

    target = tmp_path / f"stations-{ord(primitive):04x}.json"
    target.write_text(json.dumps({"stations": []}), encoding="utf-8")
    wl_entries: list[dict[str, Any]] = [
        {
            "name": f"evil{primitive}.example",
            "bst_id": "9000",
            "aliases": [f"alias{primitive}evil"],
            "latitude": 48.20,
            "longitude": 16.37,
        }
    ]
    merge_into_stations(target, wl_entries)

    raw = target.read_bytes()
    expected = _UTF8_BYTES[primitive]
    assert expected not in raw, (
        f"Trojan-Source primitive U+{ord(primitive):04X} leaked via "
        f"update_wl_stations.merge_into_stations as raw UTF-8 bytes ({expected!r})."
    )


def test_update_wl_merge_into_stations_preserves_umlauts(tmp_path: Path) -> None:
    from scripts.update_wl_stations import merge_into_stations

    target = tmp_path / "stations.json"
    target.write_text(json.dumps({"stations": []}), encoding="utf-8")
    wl_entries: list[dict[str, Any]] = [
        {
            "name": "Wien Hütteldorf",
            "bst_id": "9000",
            "aliases": ["Floridsdorfer Brücke"],
            "latitude": 48.20,
            "longitude": 16.37,
        }
    ]
    merge_into_stations(target, wl_entries)

    text = target.read_text(encoding="utf-8")
    assert "Hütteldorf" in text
    assert "\\u00fc" not in text



def test_all_script_writers_use_shared_scrubber_helper() -> None:
    """Every script-level station writer must scrub through
    ``scrub_trojan_source_primitives`` from ``src.utils.serialize`` so
    the canonical attack-byte union stays single-sourced across the
    project: directly, or through ``write_stations_payload`` of
    ``src/places/merge.py``, which calls it. A future script-level writer
    that re-implements the primitive set would silently drift away from
    the canonical floor pinned in ``src/utils/serialize.py``."""
    targets = {
        "scripts/update_all_stations.py": "write_stations_payload",
        "scripts/enrich_station_aliases.py": "write_stations_payload",
        "scripts/update_station_directory.py": "scrub_trojan_source_primitives",
        "scripts/update_wl_stations.py": "scrub_trojan_source_primitives",
        "src/places/merge.py": "scrub_trojan_source_primitives",
    }
    for rel, marker in targets.items():
        path = REPO_ROOT / rel
        text = path.read_text(encoding="utf-8")
        assert marker in text, (
            f"{rel} does not use {marker} — the canonical CVE-2021-42574 "
            f"attack-byte union is no longer single-sourced across the project."
        )
