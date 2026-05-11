"""Sentinel PoC: Trojan-Source / BiDi-Mark leakage at the two committed
operator-facing JSON sidecar writers that the *BiDi-Mark Drift Round 9*
closing-checklist named but deferred — ``_save_state`` in
``src/build_feed.py`` (``data/first_seen.json``) and the inline
heartbeat writer in ``scripts/update_all_stations.py``
(``data/stations_last_run.json``).

Both files are committed to ``main`` by the cron pipeline
(``data/first_seen.json`` is in ``build-feed.yml``'s ``file_pattern``;
``data/stations_last_run.json`` is committed by the ``update-cycle.yml``
DAG step that runs ``update_all_stations.py``). They are operator-facing
artefacts — ``cat`` / ``less`` / the GitHub web UI / IDE preview all
honour BiDi formatting controls when displaying the file.

Pre-fix both writers serialised the payload with
``json.dump(..., ensure_ascii=False, indent=2, sort_keys=True)``.
``ensure_ascii=False`` emits every non-ASCII code point as raw UTF-8 —
including the CVE-2021-42574 "Trojan Source" BiDi formatting controls
(``U+202A-U+202E`` / ``U+2066-U+2069``), zero-width primitives
(``U+200B-U+200F``), Unicode line / paragraph separators
(``U+2028``-``U+2029``), the BOM (``U+FEFF``), and the C1 terminal-
escape primitives (``\\x9b`` CSI, ``\\x9d`` OSC, ``\\x90`` DCS).

The auto-quarantine writer in
``scripts/update_all_stations.py:_write_quarantine_file`` already closed
this drift in PR #1434 (journal Round 9). The two sibling writers below
are the deferred siblings the same closing-checklist explicitly named.

Attack path for ``_save_state``
================================

  1. Attacker compromises an upstream provider (WL / OEBB / VOR cache
     poisoning, malicious station name flowing through the feed builder,
     etc.) and publishes a disruption with a title carrying
     ``\\u202e`` (RIGHT-TO-LEFT OVERRIDE) — e.g.
     ``"Westbahnhof\\u202emoc.live"`` displays as
     ``Westbahnhofevil.com`` in any BiDi-honouring viewer.
  2. ``src/build_feed.py:_identity_for_item`` computes a state-cache
     identity for the item. The WL/non-OEBB path interpolates the raw
     title verbatim — see ``build_feed.py:935`` and ``build_feed.py:944``:
     ``result = f"{base}|T={item['title']}|F={fuzzy_hash}"``.
  3. The state dict uses this identity as a top-level KEY.
     ``_save_state`` writes the dict to ``data/first_seen.json`` with
     ``ensure_ascii=False`` — the BiDi mark survives as raw UTF-8 bytes
     in the on-disk file.
  4. The build-feed CI workflow commits ``data/first_seen.json`` to
     ``main`` (its ``file_pattern`` lists this path explicitly).
  5. Operator opens the file via ``cat``, ``less``, ``git diff``, GitHub
     web UI, or IDE preview → the BiDi-reversed display of the planted
     title key hides the attack from the reviewing operator.

Attack path for the heartbeat writer
=====================================

The pre-fix inline write
(``scripts/update_all_stations.py:702-704``) used
``json.dump(heartbeat, handle, ensure_ascii=False, ...)``. The current
heartbeat payload schema only carries safe scalar values (integers,
ISO timestamps, the hard-coded ``_SCRIPT_ORDER`` names), but the
``ensure_ascii=False`` choice was structurally identical to the
quarantine writer fixed in Round 9 — the schema is dictionary-shaped,
forward-compatible, and explicitly named in the journal as the next
drift target. Any future field carrying station-controlled,
provider-controlled, or environment-derived content would leak the
canonical BiDi / line-separator / C1 union verbatim to ``cat`` / ``less``
/ the GitHub web UI.

To pin the invariant programmatically (the same shape the existing
Round-9 PoC uses for ``_write_quarantine_file``), the post-fix
heartbeat writer is wrapped in a helper ``_write_heartbeat_file`` that
uses ``ensure_ascii=True``. The PoC below uses a synthetic payload
field carrying the same Trojan-Source primitive set to demonstrate the
escape behaviour: every primitive lands as a literal ``\\uXXXX`` escape
in the on-disk file rather than its raw UTF-8 byte sequence.

Fix shape
==========

Both writers: switch from ``ensure_ascii=False`` to ``ensure_ascii=True``.

  * Forensic intent is preserved (``json.loads`` recovers the original
    string from the literal ``\\uXXXX`` escape, so debugging / replay
    is unaffected).
  * No raw BiDi / line-separator / C1 byte reaches any byte viewer
    (``cat`` / ``less`` / GitHub web UI / IDE preview).
  * Mirrors the canonical fix shape pinned in PR #1434 for
    ``_write_quarantine_file`` so the closing checklist's invariant is
    now uniform across all three committed operator-facing JSON
    sidecar writers in the ``data/*.json`` family.
"""

from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from scripts import update_all_stations as wrapper


# Canonical Trojan-Source / zero-width / Unicode-line-terminator / C1
# terminal-escape primitives — byte-exact mirror of the set pinned in
# ``tests/test_sentinel_quarantine_trojan_source.py`` so any future
# widening of the canonical floor is enforced uniformly across the
# committed-sidecar writer family.
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
# do (the JSON encoder replaces each code point with the literal
# ``\\uXXXX`` escape).
_UTF8_BYTES = {primitive: primitive.encode("utf-8") for primitive in _TROJAN_SOURCE_PRIMITIVES}


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _import_build_feed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> types.ModuleType:
    """Re-import ``src.build_feed`` with provider stubs and a tmp_path
    cwd so ``_save_state`` writes to a temp ``data/first_seen.json``
    rather than the repo's real one. Mirrors the import shape used by
    ``tests/test_first_seen_cleanup.py``.
    """
    module_name = "src.build_feed"
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.syspath_prepend(str(root / "src"))

    providers = types.ModuleType("providers")
    wl = types.ModuleType("providers.wiener_linien")
    setattr(wl, "fetch_events", lambda: [])
    oebb = types.ModuleType("providers.oebb")
    setattr(oebb, "fetch_events", lambda: [])
    vor = types.ModuleType("providers.vor")
    setattr(vor, "fetch_events", lambda: [])

    monkeypatch.setitem(sys.modules, "providers", providers)
    monkeypatch.setitem(sys.modules, "providers.wiener_linien", wl)
    monkeypatch.setitem(sys.modules, "providers.oebb", oebb)
    monkeypatch.setitem(sys.modules, "providers.vor", vor)
    sys.modules.pop(module_name, None)
    sys.modules.pop("feed", None)
    sys.modules.pop("feed.config", None)
    sys.modules.pop("src.feed", None)
    sys.modules.pop("src.feed.config", None)

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.setenv("STATE_PATH", "data/first_seen_sentinel.json")

    build_feed = importlib.import_module(module_name)
    # Allow ``validate_path`` to pass through arbitrary tmp paths.
    monkeypatch.setattr(build_feed, "validate_path", lambda p, *args: p)
    return build_feed


def _baseline_heartbeat(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a minimal heartbeat payload mirroring the schema written
    by ``scripts/update_all_stations.py:main``. The ``extra`` dict lets
    individual tests inject a synthetic field carrying the
    Trojan-Source primitive under test.
    """
    payload: dict[str, Any] = {
        "timestamp": "2026-05-10T12:00:00+00:00",
        "sub_scripts": [
            {"name": "update_station_directory.py", "exit_code": 0, "duration_s": 1.0},
        ],
        "stations": {"before": 100, "after": 100, "delta": 0},
        "validation": {
            "duplicates": 0,
            "alias_issues": 0,
            "coordinate_issues": 0,
            "gtfs_issues": 0,
            "security_issues": 0,
            "cross_station_id_issues": 0,
            "provider_issues": 0,
            "naming_issues": 0,
        },
        "diff": {"added": 0, "removed": 0, "renamed": 0, "coord_shifted": 0},
        "polygon_vertices": None,
    }
    if extra:
        payload.update(extra)
    return payload


# ---------------------------------------------------------------------
# PoC 1: ``_save_state`` writes raw BiDi marks in the state-dict key
# (the ``T={item['title']}`` path of ``_identity_for_item``).
# ---------------------------------------------------------------------


def test_save_state_does_not_emit_raw_bidi_override_in_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A feed-item title carrying U+202E (RLO) reaches the state-cache
    identity verbatim via ``_identity_for_item``'s ``T={title}`` branch
    (``src/build_feed.py:935`` / ``:944``). When the identity becomes a
    top-level key in ``data/first_seen.json`` and the writer serialises
    with ``ensure_ascii=False``, the raw UTF-8 byte sequence
    ``\\xe2\\x80\\xae`` lands in the on-disk file — operators viewing
    the file via ``cat`` / ``less`` / GitHub web UI / IDE preview see
    the BiDi-reversed display, hiding the attack from review.
    """
    build_feed = _import_build_feed(monkeypatch, tmp_path)

    malicious_title = "Westbahnhof‮moc.live"  # renders as Westbahnhofevil.com
    malicious_identity = f"wl|hinweis|L=|D=2026-05-10|T={malicious_title}|F=deadbeef"
    state = {malicious_identity: {"first_seen": "2026-05-10T12:00:00+00:00"}}

    build_feed._save_state(state)

    state_path = tmp_path / "data" / "first_seen_sentinel.json"
    raw_bytes = state_path.read_bytes()

    # The smoking gun: U+202E encodes to the 3-byte UTF-8 sequence E2 80 AE.
    # Its appearance in the on-disk file means BiDi reversal is now active
    # for any cat / less / GitHub / IDE viewer of the file.
    assert b"\xe2\x80\xae" not in raw_bytes, (
        "U+202E (RLO) leaked verbatim into data/first_seen.json — "
        "BiDi reversal is now active for any cat/less/GitHub viewer of the file."
    )
    # Defense-in-depth: the escaped form ``\\u202e`` MUST appear so the
    # forensic intent (preserve which identity was tracked) is not lost.
    assert "\\u202e" in raw_bytes.decode("utf-8"), (
        "The escaped sentinel ``\\u202e`` is missing — state data lost."
    )


@pytest.mark.parametrize("primitive", _TROJAN_SOURCE_PRIMITIVES)
def test_save_state_escapes_every_trojan_source_primitive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, primitive: str
) -> None:
    """The escape behaviour MUST be uniform across the canonical
    Trojan-Source / zero-width / line-terminator / C1 union — a single
    primitive surviving in raw form reopens the attack via the same
    state-cache key path.
    """
    build_feed = _import_build_feed(monkeypatch, tmp_path)

    malicious_identity = f"wl|hinweis|L=|D=2026-05-10|T=evil{primitive}.example|F=deadbeef"
    state = {malicious_identity: {"first_seen": "2026-05-10T12:00:00+00:00"}}

    build_feed._save_state(state)

    state_path = tmp_path / "data" / "first_seen_sentinel.json"
    raw_bytes = state_path.read_bytes()

    raw_utf8 = _UTF8_BYTES[primitive]
    assert raw_utf8 not in raw_bytes, (
        f"Trojan-Source primitive U+{ord(primitive):04X} leaked into the "
        f"state-cache file as raw UTF-8 bytes ({raw_utf8!r})."
    )


def test_save_state_preserves_legitimate_german_state_keys(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression: legitimate German station names (München, Südtirol,
    Praterstern, Westbahnhof, Floridsdorf, Schönbrunn) MUST round-trip
    through ``json.loads`` unchanged after the ``ensure_ascii=True``
    switch. The on-disk bytes are now the escape sequence
    (``M\\u00fcnchen``) but the parsed string equals the original.
    """
    build_feed = _import_build_feed(monkeypatch, tmp_path)

    state = {
        "wl|hinweis|L=|D=2026-05-10|T=Wien Westbahnhof|F=abc": {
            "first_seen": "2026-05-10T12:00:00+00:00"
        },
        "wl|hinweis|L=|D=2026-05-10|T=München Hbf|F=def": {
            "first_seen": "2026-05-10T12:00:00+00:00"
        },
        "wl|hinweis|L=|D=2026-05-10|T=Floridsdorf Schönbrunn|F=ghi": {
            "first_seen": "2026-05-10T12:00:00+00:00"
        },
    }
    build_feed._save_state(state)

    state_path = tmp_path / "data" / "first_seen_sentinel.json"
    parsed = json.loads(state_path.read_text(encoding="utf-8"))
    assert "wl|hinweis|L=|D=2026-05-10|T=München Hbf|F=def" in parsed
    assert "wl|hinweis|L=|D=2026-05-10|T=Floridsdorf Schönbrunn|F=ghi" in parsed


# ---------------------------------------------------------------------
# PoC 2: heartbeat writer — extracted ``_write_heartbeat_file`` must
# use ``ensure_ascii=True`` so a future field carrying station- /
# provider- / environment-controlled content cannot leak the canonical
# union as raw bytes into the committed ``data/stations_last_run.json``.
# ---------------------------------------------------------------------


def test_heartbeat_file_does_not_emit_raw_bidi_override(tmp_path: Path) -> None:
    """The heartbeat writer's structural fix shape: even with a payload
    carrying U+202E in a synthetic field, the on-disk file MUST NOT
    contain the raw UTF-8 byte sequence ``\\xe2\\x80\\xae``. The
    ``cat data/stations_last_run.json`` view, the GitHub web UI render,
    and any IDE preview MUST display the inert ``\\u202e`` escape rather
    than triggering BiDi reversal of the trailing characters.
    """
    payload = _baseline_heartbeat({
        "synthetic_field": "Westbahnhof‮moc.live",
    })
    out_path = tmp_path / "stations_last_run.json"
    wrapper._write_heartbeat_file(out_path, payload)

    raw_bytes = out_path.read_bytes()
    assert b"\xe2\x80\xae" not in raw_bytes, (
        "U+202E (RLO) leaked verbatim into data/stations_last_run.json — "
        "BiDi reversal is now active for any cat/less/GitHub viewer of the file."
    )
    assert "\\u202e" in raw_bytes.decode("utf-8"), (
        "The escaped sentinel ``\\u202e`` is missing — heartbeat forensic "
        "data lost."
    )


@pytest.mark.parametrize("primitive", _TROJAN_SOURCE_PRIMITIVES)
def test_heartbeat_file_escapes_every_trojan_source_primitive(
    tmp_path: Path, primitive: str
) -> None:
    """The heartbeat writer's escape behaviour MUST be uniform across
    the canonical Trojan-Source / zero-width / line-terminator / C1
    union — a single primitive surviving in raw form reopens the
    attack via a future field that carries station- / provider- /
    environment-controlled content.
    """
    payload = _baseline_heartbeat({"synthetic_field": f"evil{primitive}.example"})
    out_path = tmp_path / "stations_last_run.json"
    wrapper._write_heartbeat_file(out_path, payload)

    raw_bytes = out_path.read_bytes()
    raw_utf8 = _UTF8_BYTES[primitive]
    assert raw_utf8 not in raw_bytes, (
        f"Trojan-Source primitive U+{ord(primitive):04X} leaked into the "
        f"heartbeat file as raw UTF-8 bytes ({raw_utf8!r})."
    )


def test_heartbeat_file_round_trips_legitimate_payload(tmp_path: Path) -> None:
    """Regression: the canonical heartbeat schema (no station-controlled
    strings) round-trips byte-stable. The ASCII-only on-disk bytes
    parse via ``json.loads`` back to the same dict — operators and
    downstream parsers see the exact same structure as pre-fix runs.
    """
    payload = _baseline_heartbeat()
    out_path = tmp_path / "stations_last_run.json"
    wrapper._write_heartbeat_file(out_path, payload)

    parsed = json.loads(out_path.read_text(encoding="utf-8"))
    assert parsed == payload
    # Trailing newline preserved.
    assert out_path.read_text(encoding="utf-8").endswith("\n")


def test_heartbeat_file_preserves_german_text_via_escape(tmp_path: Path) -> None:
    """A legitimate German string (``München``) in a synthetic field
    round-trips through ``json.loads`` unchanged. The on-disk bytes are
    now the escape sequence (``M\\u00fcnchen``) but the parsed value
    equals the original — forensic intent preserved.
    """
    payload = _baseline_heartbeat({"location": "München Hbf"})
    out_path = tmp_path / "stations_last_run.json"
    wrapper._write_heartbeat_file(out_path, payload)

    parsed = json.loads(out_path.read_text(encoding="utf-8"))
    assert parsed["location"] == "München Hbf"
    # The raw multi-byte ``ü`` (UTF-8 ``\xc3\xbc``) MUST NOT appear in
    # the on-disk file; it lands as the literal ``ü`` escape.
    assert b"\xc3\xbc" not in out_path.read_bytes()
    assert "\\u00fc" in out_path.read_text(encoding="utf-8")
