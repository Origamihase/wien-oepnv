"""Sentinel PoC: Trojan-Source / BiDi-Mark leakage at the
``data/stations.json`` writer that the *BiDi-Mark Drift Round 12*
closing-checklist named but deferred — ``src/places/merge.py:write_stations``.

Round 12 (PR closing the ``write_cache`` cache-events sidecar) closed
``src/utils/cache.py:write_cache`` / ``read_cache`` via the
``scrub_trojan_source_primitives`` helper plus a preserved
``ensure_ascii=False`` flag. The Round-12 closing checklist explicitly
named ``src/places/merge.py:write_stations`` →
``data/stations.json`` as the SOLE deferred sibling in the same
operator-facing JSON family with the SAME fix shape requirement:

  * ``write_stations`` serialises the canonical station directory
    (``data/stations.json``) via
    ``json.dumps(..., ensure_ascii=False, indent=2, sort_keys=True)``
    today. The payload carries provider-fetched **German station
    names + aliases + formatted addresses** (e.g. ``Wien
    Hauptbahnhof``, ``Wien Hütteldorf``, ``Bahnhof Wien Floridsdorf``).
    A blanket ``ensure_ascii=True`` flip would balloon every umlaut /
    ``ß`` from its 2-byte UTF-8 form to the 6-byte ``\\u00XX``
    escape, ~4-6x the byte size, and bloat the weekly
    ``update-stations.yml`` commit diff.
  * The committed sidecar contract is unambiguous: the file is
    pushed to ``main`` by the weekly ``update-stations.yml`` cron
    job, whose OSM-first / Google-Places-fallback step writes the
    enriched directory.

The Round-12 "Named but deferred" entry pinned the fix shape:
**pair an ingestion-boundary Trojan-Source primitive scrubber with
the existing `ensure_ascii=False` flag** AND **reuse the
``scrub_trojan_source_primitives`` helper from Round 12** so the
defence shape stays single-sourced across every operator-facing JSON
sidecar in the codebase.

Attack path
============

1. Attacker compromises an upstream provider (Google Places hijack,
   OSM Overpass cache poisoning, OEBB ``Verzeichnis der
   Verkehrsstationen`` Excel response tampering, Wien OGD CSV
   poisoning, leaked CI secret store) or an operator mis-edits
   ``data/stations.json`` directly.
2. A planted station entry carrying U+202E (RIGHT-TO-LEFT OVERRIDE)
   in a ``name``, ``_formatted_address``, or ``aliases[]`` field —
   e.g. ``name="Hauptbahnhof‮moc.live"`` displays as
   ``Hauptbahnhofevil.com`` — reaches ``write_stations`` via one of
   the upstream Google Places ingestion paths
   (``scripts/fetch_google_places_stations.py``,
   ``scripts/update_station_directory.py``).
3. Pre-fix ``write_stations`` serialised the payload via
   ``json.dumps({"stations": ...}, ensure_ascii=False, indent=2,
   sort_keys=True)``. ``ensure_ascii=False`` emits U+202E as its raw
   UTF-8 byte triplet ``\\xe2\\x80\\xae`` directly into the on-disk
   ``data/stations.json``.
4. The ``update-stations.yml`` cron commits the poisoned
   ``data/stations.json`` to ``main`` (the workflow uses
   ``add_options: "-A"`` so any change in ``data/`` gets staged).
   The malicious name is now visible in ``git log -p`` /
   ``git show`` / the GitHub web UI / ``cat`` / ``less`` /
   IDE preview — every viewer honours BiDi reversal.
5. Operator reviewing the commit / cache file for diff bloat /
   suspicious upstream behaviour misreads the BiDi-reversed display
   as the inverse of the actual planted bytes. The attack hides in
   the operator's own review pass.

Canonical attack-byte union
============================

Byte-exact mirror of the union pinned in
``tests/test_sentinel_cache_events_trojan_source.py`` (Round 12) so
any future widening of the canonical floor stays uniform across the
committed-sidecar writer family:

  * BiDi formatting controls (CVE-2021-42574 first half):
    ``U+202A``-``U+202E`` (LRE/RLE/PDF/LRO/RLO).
  * BiDi isolates (CVE-2021-42574 second half):
    ``U+2066``-``U+2069`` (LRI/RLI/FSI/PDI).
  * Zero-width primitives + LRM/RLM/ALM:
    ``U+200B``-``U+200F``, ``U+061C``.
  * Unicode line / paragraph separators:
    ``U+2028``, ``U+2029``.
  * Byte Order Mark / ZWNBSP: ``U+FEFF``.
  * 8-bit C1 terminal-escape primitives:
    ``\\x9b`` (CSI), ``\\x9d`` (OSC), ``\\x90`` (DCS).

Fix shape
==========

  * Reuse ``src/utils/serialize.py:scrub_trojan_source_primitives``
    (added in Round 12) so the canonical attack-byte union stays
    single-sourced across every operator-facing JSON sidecar writer.
  * ``src/places/merge.py:write_stations``: apply the scrubber to
    the incoming ``stations`` BEFORE ``json.dumps`` —
    ingestion-boundary defence so the dangerous bytes never reach
    the writer.
  * ``src/places/merge.py:load_stations``: apply the same scrubber
    to the parsed payload BEFORE returning to callers —
    defence-in-depth at the read boundary that retroactively cleans
    any historic poisoned ``data/stations.json`` (planted before
    this fix, surviving on disk through a corrupted previous run,
    or written by a future bypass of the write-side scrubber).
  * Keep ``ensure_ascii=False`` so legitimate German content
    (ä/ö/ü/ß + every other safe Unicode code point) stays compact
    in the on-disk diff. The scrubber removes ONLY the canonical
    attack-byte union — every safe non-ASCII code point passes
    through unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.places.merge import load_stations, write_stations


# Canonical Trojan-Source / zero-width / Unicode-line-terminator / C1
# terminal-escape primitives — byte-exact mirror of the set pinned in
# ``tests/test_sentinel_cache_events_trojan_source.py`` (Round 12) so
# any future widening of the canonical floor is enforced uniformly
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
    # C1 terminal escape primitives (8-bit colour/SGR start, OSC).
    "\x9b",  # CSI
    "\x9d",  # OSC
    "\x90",  # DCS
)

# UTF-8 byte sequence each primitive encodes to. Pre-fix every byte
# sequence appears in the on-disk file verbatim; post-fix none of them
# do (the scrubber strips each code point at the ingestion boundary).
_UTF8_BYTES = {
    primitive: primitive.encode("utf-8") for primitive in _TROJAN_SOURCE_PRIMITIVES
}


# ---------------------------------------------------------------------
# PoC 1: ``write_stations`` — the canonical RLO byte triplet MUST NOT
# leak into the on-disk file.
# ---------------------------------------------------------------------


def test_write_stations_does_not_emit_raw_bidi_override(tmp_path: Path) -> None:
    """A planted station entry carrying U+202E reaches the on-disk
    ``data/stations.json`` verbatim pre-fix. Operators viewing the file
    via ``cat`` / ``less`` / GitHub web UI / IDE preview see the
    BiDi-reversed display of the surrounding bytes, hiding the attack
    from review.
    """
    target = tmp_path / "stations.json"
    stations = [
        {
            "name": "Hauptbahnhof‮moc.live",
            "source": "google_places",
            "latitude": 48.18,
            "longitude": 16.38,
        }
    ]
    write_stations(target, stations)  # type: ignore[arg-type]

    raw_bytes = target.read_bytes()
    # The smoking gun: U+202E encodes to the 3-byte UTF-8 sequence E2 80 AE.
    # Its appearance in the on-disk file means BiDi reversal is now active
    # for any cat / less / GitHub / IDE viewer of the file.
    assert b"\xe2\x80\xae" not in raw_bytes, (
        "U+202E (RLO) leaked verbatim into data/stations.json — "
        "BiDi reversal is now active for any cat/less/GitHub viewer of the file."
    )


@pytest.mark.parametrize("primitive", _TROJAN_SOURCE_PRIMITIVES)
def test_write_stations_strips_every_trojan_source_primitive(
    tmp_path: Path, primitive: str
) -> None:
    """The scrubber's reject-set MUST be uniform across the canonical
    Trojan-Source / zero-width / line-terminator / C1 union — a single
    primitive surviving in raw form reopens the attack via a future
    name / aliases / formatted-address field carrying provider-fetched
    content.
    """
    target = tmp_path / f"stations-{ord(primitive):04x}.json"
    stations = [
        {
            "name": f"evil{primitive}.example",
            "source": "google_places",
            "latitude": 48.18,
            "longitude": 16.38,
        }
    ]
    write_stations(target, stations)  # type: ignore[arg-type]

    raw_bytes = target.read_bytes()
    raw_utf8 = _UTF8_BYTES[primitive]
    assert raw_utf8 not in raw_bytes, (
        f"Trojan-Source primitive U+{ord(primitive):04X} leaked into "
        f"data/stations.json as raw UTF-8 bytes ({raw_utf8!r})."
    )


def test_write_stations_strips_primitive_in_aliases_list(tmp_path: Path) -> None:
    """The ``aliases`` field is a ``list[str]`` — a critical attack
    surface that the recursive scrubber MUST cover. A planted alias
    landing in raw form on disk would still be rendered by the GitHub
    diff view.
    """
    target = tmp_path / "stations.json"
    stations = [
        {
            "name": "Hauptbahnhof",
            "source": "google_places",
            "latitude": 48.18,
            "longitude": 16.38,
            "aliases": ["Wien Hauptbahnhof", "Hbf‮moc.live"],
        }
    ]
    write_stations(target, stations)  # type: ignore[arg-type]

    raw_bytes = target.read_bytes()
    assert b"\xe2\x80\xae" not in raw_bytes, (
        "U+202E leaked from a nested aliases[] element into "
        "data/stations.json."
    )


def test_write_stations_strips_primitive_in_formatted_address(tmp_path: Path) -> None:
    """``_formatted_address`` is provider-fetched from Google Places —
    a hijacked / cache-poisoned upstream could plant the primitive
    there. The scrubber MUST cover string values regardless of key
    name.
    """
    target = tmp_path / "stations.json"
    stations = [
        {
            "name": "Hauptbahnhof",
            "source": "google_places",
            "latitude": 48.18,
            "longitude": 16.38,
            "_formatted_address": "Am Hauptbahnhof 1, 1100‮moc.live",
        }
    ]
    write_stations(target, stations)  # type: ignore[arg-type]

    raw_bytes = target.read_bytes()
    assert b"\xe2\x80\xae" not in raw_bytes, (
        "U+202E leaked from _formatted_address into data/stations.json."
    )


def test_write_stations_strips_primitive_in_dict_key(tmp_path: Path) -> None:
    """Dict KEYS are an attack surface too — a future schema-drift
    addition or operator mis-edit could plant the primitive in a key
    name. The scrubber MUST cover keys as well as values.
    """
    target = tmp_path / "stations.json"
    stations = [
        {
            "name‮_evil": "harmless",
            "source": "google_places",
            "latitude": 48.18,
            "longitude": 16.38,
        }
    ]
    write_stations(target, stations)  # type: ignore[arg-type]

    raw_bytes = target.read_bytes()
    assert b"\xe2\x80\xae" not in raw_bytes, (
        "U+202E leaked into a dict KEY of data/stations.json — "
        "BiDi reversal is now active for any viewer of the file."
    )


def test_write_stations_strips_primitive_in_types_list(tmp_path: Path) -> None:
    """``_types`` is a Google Places provider-fetched ``list[str]``
    field. Cache poisoning of the Places API response could plant a
    primitive there. The scrubber MUST recurse into nested lists.
    """
    target = tmp_path / "stations.json"
    stations = [
        {
            "name": "Hauptbahnhof",
            "source": "google_places",
            "latitude": 48.18,
            "longitude": 16.38,
            "_types": ["train_station", "transit_station‮moc.live"],
        }
    ]
    write_stations(target, stations)  # type: ignore[arg-type]

    raw_bytes = target.read_bytes()
    assert b"\xe2\x80\xae" not in raw_bytes, (
        "U+202E leaked from _types[] into data/stations.json."
    )


# ---------------------------------------------------------------------
# Regression: legitimate German content stays compact (no diff bloat).
# ---------------------------------------------------------------------


def test_write_stations_preserves_german_umlauts_as_raw_utf8(tmp_path: Path) -> None:
    """The fix-shape contract: ``ensure_ascii=False`` is preserved so
    legitimate German station names (umlauts ä/ö/ü/Ä/Ö/Ü, sharp s ß)
    stay as raw UTF-8 in the on-disk file — every weekly
    ``update-stations.yml`` commit diff stays compact. A blanket
    ``ensure_ascii=True`` flip would balloon each German character to
    a 6-byte ``\\u00XX`` escape; the scrubber + flag-preservation
    contract avoids that bloat.
    """
    target = tmp_path / "stations.json"
    stations = [
        {
            "name": "Wien Hütteldorf — Großbahnhof Mariä-Empfängnis",
            "source": "oebb",
            "latitude": 48.19,
            "longitude": 16.26,
            "aliases": ["Hütteldorf", "Mariä-Empfängnis-Bahnhof"],
        }
    ]
    write_stations(target, stations)  # type: ignore[arg-type]

    raw_bytes = target.read_bytes()
    # Every German character MUST land as raw UTF-8, not the 6-byte
    # ``\\u00XX`` escape sequence.
    assert "ß".encode() in raw_bytes, "ß lost as raw UTF-8 — diff bloat!"
    assert "ü".encode() in raw_bytes, "ü lost as raw UTF-8 — diff bloat!"
    assert "ä".encode() in raw_bytes, "ä lost as raw UTF-8 — diff bloat!"
    # Defence in depth: the literal ``\\u00fc`` escape (the bloated
    # form) MUST NOT appear in the on-disk file.
    decoded = raw_bytes.decode("utf-8")
    assert "\\u00fc" not in decoded, (
        "ü was escaped as \\u00fc — ensure_ascii=False contract broken, "
        "compact German diff lost."
    )
    assert "\\u00e4" not in decoded, (
        "ä was escaped as \\u00e4 — ensure_ascii=False contract broken."
    )


def test_write_stations_preserves_clean_payload_round_trip(tmp_path: Path) -> None:
    """Regression: a clean payload (no Trojan-Source primitives)
    round-trips byte-stable through the scrubber. ``load_stations``
    recovers exactly the same dict every caller saw pre-fix.
    """
    target = tmp_path / "stations.json"
    stations = [
        {
            "name": "Wien Hauptbahnhof",
            "source": "google_places,oebb",
            "latitude": 48.18,
            "longitude": 16.38,
            "aliases": ["Hauptbahnhof", "Wien Hbf"],
            "_google_place_id": "ChIJqaSC1234567890",
            "_types": ["train_station", "transit_station"],
        }
    ]
    write_stations(target, stations)  # type: ignore[arg-type]

    parsed = load_stations(target)
    assert parsed == stations


# ---------------------------------------------------------------------
# Defence in depth: ``load_stations`` strips primitives from a poisoned
# on-disk stations file (planted before the fix, surviving from a
# corrupted previous run, or written by a future bypass).
# ---------------------------------------------------------------------


def test_load_stations_strips_pre_existing_trojan_source_primitives(
    tmp_path: Path,
) -> None:
    """A pre-existing ``data/stations.json`` carrying U+202E in raw
    UTF-8 form (planted before this fix or written by a future bypass
    of ``write_stations``'s scrubber) MUST NOT propagate the primitive
    into the in-memory data structure handed to callers.
    ``load_stations`` strips the canonical attack-byte union on the
    way out.
    """
    target = tmp_path / "stations.json"
    # Plant the poisoned bytes directly — bypasses ``write_stations``.
    poisoned = json.dumps(
        {
            "stations": [
                {
                    "name": "Hauptbahnhof‮moc.live",
                    "source": "google_places",
                    "latitude": 48.18,
                    "longitude": 16.38,
                }
            ]
        },
        ensure_ascii=False,
    )
    target.write_text(poisoned, encoding="utf-8")
    # Sanity: the planted file actually contains the dangerous bytes.
    assert b"\xe2\x80\xae" in target.read_bytes()

    stations = load_stations(target)

    assert isinstance(stations, list)
    assert len(stations) == 1
    name = stations[0]["name"]
    assert "‮" not in name, (
        "load_stations returned a string still carrying U+202E — "
        "defence in depth at the read boundary failed."
    )


@pytest.mark.parametrize("primitive", _TROJAN_SOURCE_PRIMITIVES)
def test_load_stations_strips_every_trojan_source_primitive(
    tmp_path: Path, primitive: str
) -> None:
    """The defence-in-depth read-boundary scrubber MUST cover the same
    canonical union as the write-boundary scrubber.
    """
    target = tmp_path / f"stations-{ord(primitive):04x}.json"
    poisoned = json.dumps(
        {
            "stations": [
                {
                    "name": f"evil{primitive}.example",
                    "source": "google_places",
                }
            ]
        },
        ensure_ascii=False,
    )
    target.write_text(poisoned, encoding="utf-8")

    stations = load_stations(target)

    assert len(stations) == 1
    name = stations[0]["name"]
    assert primitive not in name, (
        f"load_stations returned a string still carrying U+{ord(primitive):04X} "
        f"— read-boundary scrubber drift."
    )


def test_load_stations_preserves_german_umlauts(tmp_path: Path) -> None:
    """Regression: the read-boundary scrubber MUST NOT touch
    legitimate German content. ä/ö/ü/ß round-trip identically.
    """
    target = tmp_path / "stations.json"
    payload = {
        "stations": [
            {
                "name": "Wien Hütteldorf",
                "source": "oebb",
                "latitude": 48.19,
                "longitude": 16.26,
                "aliases": ["Hütteldorf", "Großbahnhof"],
            }
        ]
    }
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    stations = load_stations(target)

    assert stations == payload["stations"]


def test_load_stations_handles_list_root_shape(tmp_path: Path) -> None:
    """``load_stations`` supports both ``{"stations": [...]}`` and bare
    ``[...]`` root shapes. The read-boundary scrubber MUST cover both.
    """
    target = tmp_path / "stations.json"
    # Bare list root shape with planted U+202E.
    poisoned = json.dumps(
        [
            {
                "name": "Hauptbahnhof‮moc.live",
                "source": "google_places",
            }
        ],
        ensure_ascii=False,
    )
    target.write_text(poisoned, encoding="utf-8")
    assert b"\xe2\x80\xae" in target.read_bytes()

    stations = load_stations(target)

    assert len(stations) == 1
    name = stations[0]["name"]
    assert "‮" not in name
