"""Sentinel PoC: Trojan-Source / BiDi-Mark leakage at the three
committed operator-facing JSON sidecar writers that the *BiDi-Mark
Drift Round 10* closing-checklist named but deferred —
``MonthlyQuota.save_atomic`` (``data/places_quota.json``),
``save_request_count``'s atomic-write site
(``data/vor_request_count.json``), and ``write_status``
(``cache/<provider>/last_run.json``).

All three files are committed to ``main`` by the cron pipeline:

  * ``data/places_quota.json`` — ``update-google-places-stations.yml``
    line 160 (``git add data/places_quota.json``).
  * ``data/vor_request_count.json`` — ``update-vor-cache.yml`` line 97
    (``file_pattern: data/vor_request_count.json``) and
    ``update-stammstrecke-status.yml`` line 96 (same file_pattern).
  * ``cache/vor*/last_run.json`` — ``update-vor-cache.yml`` line 96
    (``file_pattern: cache/vor*/last_run.json``).

They are operator-facing artefacts — ``cat`` / ``less`` / the GitHub
web UI / IDE preview all honour BiDi formatting controls when
displaying the file. Pre-fix every writer serialised the payload with
``json.dump(..., ensure_ascii=False, ...)``. ``ensure_ascii=False``
emits every non-ASCII code point as raw UTF-8 — including the
CVE-2021-42574 "Trojan Source" BiDi formatting controls
(``U+202A``-``U+202E`` / ``U+2066``-``U+2069``), zero-width primitives
(``U+200B``-``U+200F``), Unicode line / paragraph separators
(``U+2028`` / ``U+2029``), the BOM (``U+FEFF``), and the C1 terminal-
escape primitives (``\\x9b`` CSI, ``\\x9d`` OSC, ``\\x90`` DCS).

The state-cache writer (``_save_state``), the orchestrator heartbeat
writer (``_write_heartbeat_file``), the auto-quarantine writer
(``_write_quarantine_file``) and the stations-diff markdown writer
(``_render_diff_markdown``) already closed this drift in PR #1434 and
PR #1435 (journal Rounds 9 and 10). The three sibling writers below
are the deferred siblings the Round-10 closing-checklist explicitly
named.

Attack path for ``MonthlyQuota.save_atomic``
=============================================

The current schema serialises ``month_key``, ``daily_key``,
``counts`` (a ``dict[str, int]``), ``total`` and ``daily_total`` —
``month_key`` and ``daily_key`` are *strings*. Today both fields are
populated from internal helpers (``current_month_key`` /
``current_daily_key`` return safe ASCII), but the dataclass itself
accepts any string. A future schema-drift addition or an operator
mis-edit of the on-disk file would slip a planted BiDi mark straight
through to the next ``save_atomic`` write — the file is committed to
``main`` by ``update-google-places-stations.yml`` and rendered via
``cat`` / ``less`` / the GitHub web UI / IDE preview, where the BiDi
reversal of the surrounding bytes hides the attack from review.

Attack path for ``save_request_count``'s writer
================================================

The pre-fix inline write at ``src/providers/vor.py:1562`` used
``json.dump(payload, handle, ensure_ascii=False)``. The current payload
schema is ``{"date": <ISO date>, "requests": <int>}`` — both safe
scalars — but the schema is dictionary-shaped and forward-compatible.
A future field carrying station- / provider- / environment-controlled
content (e.g. ``"last_user_agent"``, ``"last_error_token"``) would leak
the canonical BiDi / line-separator / C1 union verbatim to ``cat`` /
``less`` / the GitHub web UI.

Attack path for ``write_status``
=================================

The pre-fix write at ``src/utils/cache.py:435`` used
``json.dump(status, fh, ensure_ascii=False, indent=2, sort_keys=True)``.
The current ``status`` schema is heartbeat-shaped (``"status"`` token,
ISO timestamps, integers), but the callers can pass any dict — for
example, ``scripts/update_vor_cache.py:_record_status`` constructs the
payload from the run's runtime state, and a future field carrying a
provider-controlled error fragment, station name, or request-id would
slip through.

To pin the invariant programmatically (the same shape the existing
Round-9 / Round-10 PoCs use), each post-fix writer uses
``ensure_ascii=True``. The PoCs below use a synthetic payload field
(or, for ``MonthlyQuota``, the existing ``daily_key`` string field)
carrying the canonical Trojan-Source primitive set to demonstrate the
escape behaviour: every primitive lands as a literal ``\\uXXXX`` escape
in the on-disk file rather than its raw UTF-8 byte sequence.

Fix shape
==========

All three writers: switch from ``ensure_ascii=False`` to
``ensure_ascii=True``. The VOR writer additionally extracts the inline
``atomic_write`` block into a ``_write_request_count_file(path,
payload)`` helper, mirroring the Round-10 ``_write_heartbeat_file``
extraction so the canonical fix-shape lives in exactly one place per
writer family.

  * Forensic intent is preserved (``json.loads`` recovers the original
    string from the literal ``\\uXXXX`` escape, so debugging / replay
    is unaffected).
  * No raw BiDi / line-separator / C1 byte reaches any byte viewer
    (``cat`` / ``less`` / GitHub web UI / IDE preview).
  * The closing-checklist's invariant is now uniform across every
    committed operator-facing JSON sidecar writer in the
    ``data/*.json`` and ``cache/<provider>/*.json`` family.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.places.quota import MonthlyQuota
from src.providers.vor import _write_request_count_file
from src.utils import cache
from src.utils.files import sanitize_filename


# Canonical Trojan-Source / zero-width / Unicode-line-terminator / C1
# terminal-escape primitives — byte-exact mirror of the set pinned in
# ``tests/test_sentinel_state_heartbeat_trojan_source.py`` so any future
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
# PoC 1: ``MonthlyQuota.save_atomic`` — the ``daily_key`` / ``month_key``
# string fields MUST NOT leak raw BiDi marks into the on-disk file.
# ---------------------------------------------------------------------


def test_quota_save_atomic_does_not_emit_raw_bidi_override(tmp_path: Path) -> None:
    """A planted ``daily_key`` carrying U+202E reaches the on-disk
    ``data/places_quota.json`` verbatim pre-fix. Operators viewing the
    file via ``cat`` / ``less`` / GitHub web UI / IDE preview see the
    BiDi-reversed display of the surrounding bytes, hiding the attack
    from review.
    """
    quota = MonthlyQuota(
        month_key="2026-05",
        counts={"nearby": 1, "text": 0, "details": 0},
        total=1,
        daily_key="2026-05-10‮moc.live",
        daily_total=1,
    )
    out_path = tmp_path / "places_quota.json"
    quota.save_atomic(out_path)

    raw_bytes = out_path.read_bytes()
    # The smoking gun: U+202E encodes to the 3-byte UTF-8 sequence E2 80 AE.
    # Its appearance in the on-disk file means BiDi reversal is now active
    # for any cat / less / GitHub / IDE viewer of the file.
    assert b"\xe2\x80\xae" not in raw_bytes, (
        "U+202E (RLO) leaked verbatim into data/places_quota.json — "
        "BiDi reversal is now active for any cat/less/GitHub viewer of the file."
    )
    # Defense-in-depth: the escaped form ``\\u202e`` MUST appear so the
    # forensic intent (preserve which date / key was tracked) is not lost.
    assert "\\u202e" in raw_bytes.decode("utf-8"), (
        "The escaped sentinel ``\\u202e`` is missing — quota state lost."
    )


@pytest.mark.parametrize("primitive", _TROJAN_SOURCE_PRIMITIVES)
def test_quota_save_atomic_escapes_every_trojan_source_primitive(
    tmp_path: Path, primitive: str
) -> None:
    """The escape behaviour MUST be uniform across the canonical
    Trojan-Source / zero-width / line-terminator / C1 union — a single
    primitive surviving in raw form reopens the attack via a future
    field that carries operator-controlled or schema-drift content.
    """
    quota = MonthlyQuota(
        month_key="2026-05",
        daily_key=f"evil{primitive}.example",
        daily_total=1,
        total=1,
    )
    out_path = tmp_path / "places_quota.json"
    quota.save_atomic(out_path)

    raw_bytes = out_path.read_bytes()
    raw_utf8 = _UTF8_BYTES[primitive]
    assert raw_utf8 not in raw_bytes, (
        f"Trojan-Source primitive U+{ord(primitive):04X} leaked into the "
        f"places quota state file as raw UTF-8 bytes ({raw_utf8!r})."
    )


def test_quota_save_atomic_round_trips_legitimate_payload(tmp_path: Path) -> None:
    """Regression: the canonical quota schema (ASCII-safe ISO keys + ints)
    round-trips byte-stable through ``json.loads``. The ``MonthlyQuota.load``
    path must continue to recover the in-memory state from the on-disk file
    after the ``ensure_ascii=True`` switch — operators relying on the file
    for monthly reconciliation see exactly the same parsed structure as
    pre-fix runs.
    """
    quota = MonthlyQuota(
        month_key="2026-05",
        counts={"nearby": 3, "text": 2, "details": 1},
        total=6,
        daily_key="2026-05-10",
        daily_total=4,
    )
    out_path = tmp_path / "places_quota.json"
    quota.save_atomic(out_path)

    parsed = json.loads(out_path.read_text(encoding="utf-8"))
    assert parsed["month"] == "2026-05"
    assert parsed["counts"] == {"nearby": 3, "text": 2, "details": 1}
    assert parsed["total"] == 6
    assert parsed["daily_key"] == "2026-05-10"
    assert parsed["daily_total"] == 4


# ---------------------------------------------------------------------
# PoC 2: ``_write_request_count_file`` — the extracted VOR quota writer
# helper MUST NOT leak raw BiDi marks into
# ``data/vor_request_count.json``.
# ---------------------------------------------------------------------


def test_vor_request_count_does_not_emit_raw_bidi_override(tmp_path: Path) -> None:
    """The VOR quota writer's structural fix shape: even with a payload
    carrying U+202E in a synthetic field, the on-disk file MUST NOT
    contain the raw UTF-8 byte sequence ``\\xe2\\x80\\xae``. The
    ``cat data/vor_request_count.json`` view, the GitHub web UI render,
    and any IDE preview MUST display the inert ``\\u202e`` escape rather
    than triggering BiDi reversal of the trailing characters.
    """
    payload = {
        "date": "2026-05-10‮moc.live",
        "requests": 42,
    }
    out_path = tmp_path / "vor_request_count.json"
    _write_request_count_file(out_path, payload)

    raw_bytes = out_path.read_bytes()
    assert b"\xe2\x80\xae" not in raw_bytes, (
        "U+202E (RLO) leaked verbatim into data/vor_request_count.json — "
        "BiDi reversal is now active for any cat/less/GitHub viewer of the file."
    )
    assert "\\u202e" in raw_bytes.decode("utf-8"), (
        "The escaped sentinel ``\\u202e`` is missing — VOR quota forensic "
        "data lost."
    )


@pytest.mark.parametrize("primitive", _TROJAN_SOURCE_PRIMITIVES)
def test_vor_request_count_escapes_every_trojan_source_primitive(
    tmp_path: Path, primitive: str
) -> None:
    """The VOR quota writer's escape behaviour MUST be uniform across
    the canonical Trojan-Source / zero-width / line-terminator / C1
    union — a single primitive surviving in raw form reopens the
    attack via a future field that carries station- / provider- /
    environment-controlled content.
    """
    payload = {"date": f"evil{primitive}.example", "requests": 1}
    out_path = tmp_path / "vor_request_count.json"
    _write_request_count_file(out_path, payload)

    raw_bytes = out_path.read_bytes()
    raw_utf8 = _UTF8_BYTES[primitive]
    assert raw_utf8 not in raw_bytes, (
        f"Trojan-Source primitive U+{ord(primitive):04X} leaked into the "
        f"VOR quota file as raw UTF-8 bytes ({raw_utf8!r})."
    )


def test_vor_request_count_round_trips_legitimate_payload(tmp_path: Path) -> None:
    """Regression: the canonical VOR quota schema (``date`` + ``requests``)
    round-trips byte-stable. The ASCII-only on-disk bytes parse via
    ``json.loads`` back to the same dict — operators and downstream
    parsers see the exact same structure as pre-fix runs.
    """
    payload = {"date": "2026-05-10", "requests": 7}
    out_path = tmp_path / "vor_request_count.json"
    _write_request_count_file(out_path, payload)

    parsed = json.loads(out_path.read_text(encoding="utf-8"))
    assert parsed == payload
    # Trailing newline preserved.
    assert out_path.read_text(encoding="utf-8").endswith("\n")


# ---------------------------------------------------------------------
# PoC 3: ``write_status`` — the cache heartbeat writer MUST NOT leak
# raw BiDi marks into ``cache/<provider>/last_run.json``.
# ---------------------------------------------------------------------


def test_write_status_does_not_emit_raw_bidi_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A planted ``status`` payload carrying U+202E in a synthetic
    field reaches ``cache/<provider>/last_run.json`` verbatim pre-fix.
    Operators viewing the file via ``cat`` / ``less`` / GitHub web UI
    / IDE preview see the BiDi-reversed display of the surrounding
    bytes, hiding the attack from review.
    """
    base = tmp_path / "cache-root"
    monkeypatch.setattr(cache, "_CACHE_DIR", base, raising=False)

    payload = {
        "last_run_at": "2026-05-10T12:00:00+00:00",
        "status": "ok",
        "error_token": "evil‮moc.live",
    }
    cache.write_status("vor", payload)

    status_path = base / sanitize_filename("vor") / "last_run.json"
    raw_bytes = status_path.read_bytes()
    assert b"\xe2\x80\xae" not in raw_bytes, (
        "U+202E (RLO) leaked verbatim into cache/<provider>/last_run.json — "
        "BiDi reversal is now active for any cat/less/GitHub viewer of the file."
    )
    assert "\\u202e" in raw_bytes.decode("utf-8"), (
        "The escaped sentinel ``\\u202e`` is missing — cache status "
        "forensic data lost."
    )


@pytest.mark.parametrize("primitive", _TROJAN_SOURCE_PRIMITIVES)
def test_write_status_escapes_every_trojan_source_primitive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, primitive: str
) -> None:
    """The cache status writer's escape behaviour MUST be uniform
    across the canonical Trojan-Source / zero-width / line-terminator
    / C1 union — a single primitive surviving in raw form reopens the
    attack via a future field that carries station- / provider- /
    environment-controlled content.
    """
    base = tmp_path / "cache-root"
    monkeypatch.setattr(cache, "_CACHE_DIR", base, raising=False)

    payload = {"status": "ok", "error_token": f"evil{primitive}.example"}
    cache.write_status("vor", payload)

    status_path = base / sanitize_filename("vor") / "last_run.json"
    raw_bytes = status_path.read_bytes()
    raw_utf8 = _UTF8_BYTES[primitive]
    assert raw_utf8 not in raw_bytes, (
        f"Trojan-Source primitive U+{ord(primitive):04X} leaked into the "
        f"cache status file as raw UTF-8 bytes ({raw_utf8!r})."
    )


def test_write_status_round_trips_legitimate_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: the canonical heartbeat schema (status token, ISO
    timestamps, integers) round-trips byte-stable. The ASCII-only
    on-disk bytes parse via ``json.loads`` back to the same dict —
    downstream ``read_status`` callers see the exact same structure
    as pre-fix runs.
    """
    base = tmp_path / "cache-root"
    monkeypatch.setattr(cache, "_CACHE_DIR", base, raising=False)

    payload = {
        "last_run_at": "2026-05-10T12:00:00+00:00",
        "status": "ok",
        "events_collected": 5,
        "stations_queried": 2,
    }
    cache.write_status("vor", payload)

    assert cache.read_status("vor") == payload


def test_write_status_preserves_german_text_via_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A legitimate German string (``München``) in a synthetic status
    field round-trips through ``json.loads`` unchanged. The on-disk
    bytes are now the escape sequence (``M\\u00fcnchen``) but the
    parsed value equals the original — forensic intent preserved.
    """
    base = tmp_path / "cache-root"
    monkeypatch.setattr(cache, "_CACHE_DIR", base, raising=False)

    payload = {"status": "ok", "location": "München Hbf"}
    cache.write_status("vor", payload)

    status_path = base / sanitize_filename("vor") / "last_run.json"
    parsed = json.loads(status_path.read_text(encoding="utf-8"))
    assert parsed["location"] == "München Hbf"
    # The raw multi-byte ``ü`` (UTF-8 ``\xc3\xbc``) MUST NOT appear in
    # the on-disk file; it lands as the literal ``ü`` escape.
    assert b"\xc3\xbc" not in status_path.read_bytes()
    assert "\\u00fc" in status_path.read_text(encoding="utf-8")
