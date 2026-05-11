"""Sentinel PoC: Trojan-Source / BiDi-Mark leakage at the
``cache/<provider>/events.json`` writer that the *BiDi-Mark Drift
Round 11* closing-checklist named but deferred —
``src/utils/cache.py:write_cache``.

Round 11 (PR #1436) closed the three sibling writers
(``MonthlyQuota.save_atomic`` → ``data/places_quota.json``,
``_write_request_count_file`` → ``data/vor_request_count.json``,
``write_status`` → ``cache/<provider>/last_run.json``). The Round-11
closing checklist explicitly named ``write_cache`` as the sole
deferred sidecar in the same operator-facing JSON family — but with a
DIFFERENT fix shape than the prior rounds:

  * The other writers carried only safe scalar values (ISO dates,
    integers, hard-coded status tokens). Switching to
    ``ensure_ascii=True`` had a negligible diff cost.
  * ``write_cache`` carries provider-fetched **German titles +
    descriptions** (umlauts ä/ö/ü/ß ≡ U+00E4/U+00F6/U+00FC/U+00DF,
    plus the German sharp s ß ≡ U+00DF, plus capitalised forms).
    A blanket ``ensure_ascii=True`` flip would massively bloat the
    committed cache diff — every ``ü`` becomes ``\\u00fc``, every
    description sentence balloons to 4-6x its original size in the
    on-disk byte representation.

The closing checklist therefore pinned the fix shape: pair a
**Trojan-Source primitive scrubber at the ingestion boundary** with
the existing ``ensure_ascii=False`` flag so the committed diff stays
compact for legitimate German content while the canonical
CVE-2021-42574 attack-byte union is rejected before reaching the
writer.

Attack path
============

1. Attacker compromises an upstream provider (WL / OEBB / VOR cache
   poisoning, malicious title flowing through ``update_*_cache.py``,
   DNS hijack of an unpinned upstream, leaked CI secret store) or an
   operator mis-edits the on-disk cache file.
2. A planted item carrying U+202E (RIGHT-TO-LEFT OVERRIDE) — e.g.
   ``Westbahnhof‮moc.live`` displays as ``Westbahnhofevil.com`` —
   reaches ``write_cache`` via one of the four ``update_*_cache.py``
   call sites:
     * ``scripts/update_wl_cache.py`` line 59
     * ``scripts/update_oebb_cache.py`` line 67
     * ``scripts/update_vor_cache.py`` line 234
     * ``scripts/update_baustellen_cache.py`` line 570
3. Pre-fix ``write_cache`` serialised the item via
   ``json.dump(items, fh, ensure_ascii=False, ...)``.
   ``ensure_ascii=False`` emits U+202E as its raw UTF-8 byte triplet
   ``\\xe2\\x80\\xae``, so the on-disk
   ``cache/<provider>/events.json`` carries the BiDi-reversal trigger.
4. The respective workflow commits ``cache/<provider>/events.json`` to
   ``main`` (e.g. ``update-vor-cache.yml`` line 96 lists
   ``cache/vor/events.json`` in its ``file_pattern``). The malicious
   title is now visible in ``git log -p`` / ``git show`` / the
   GitHub web UI / ``cat`` / ``less`` / IDE preview — every viewer
   honours BiDi reversal.

Canonical attack-byte union
============================

Mirror of the union pinned in
``tests/test_sentinel_quota_status_trojan_source.py``:

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

  * ``src/utils/serialize.py``: add a recursive
    ``scrub_trojan_source_primitives`` helper that walks JSON-shaped
    structures (dict / list / tuple / scalar) and strips the canonical
    union from every string value AND every dict KEY.
  * ``src/utils/cache.py:write_cache``: apply the scrubber to the
    incoming ``items`` BEFORE the data-degradation guard count, sort,
    and ``json.dump`` — ingestion-boundary defence so the dangerous
    bytes never reach the writer.
  * ``src/utils/cache.py:read_cache``: apply the same scrubber to the
    parsed payload BEFORE returning to callers — defence-in-depth at
    the read boundary that retroactively cleans any historic poisoned
    cache file (planted before this fix, surviving on disk through
    a corrupted previous run, or written by a future bypass of the
    write-side scrubber).
  * Keep ``ensure_ascii=False`` so legitimate German content (ä/ö/ü/ß
    + CJK + every other non-ASCII code point that is NOT in the
    Trojan-Source union) stays compact in the on-disk diff. The
    scrubber removes ONLY the canonical attack-byte union — every
    safe non-ASCII code point passes through unchanged.

The scrubber returns the input structure with all Trojan-Source
primitives REMOVED (not escaped). Forensic intent is intentionally
not preserved at the cache-content layer because:

  * Cache content is a forward-flowing data feed — the scrub-and-drop
    semantics match the pattern used at every other text-content
    sink (``normalise_markdown_text``, the CSV C0 stripper, the
    log-injection regex). Operators reading the cache file see the
    safe portion of the title; the dangerous bytes are gone.
  * The ``ensure_ascii=True`` escape-and-preserve semantics used by
    the sibling state writers fits *state* sinks (heartbeat, quota,
    request count) where each row is a unique sentinel and forensic
    re-construction matters. Cache content has high cardinality
    (thousands of items per refresh) and per-item forensic
    re-construction would just bloat the diff.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.utils import cache
from src.utils.files import sanitize_filename


# Canonical Trojan-Source / zero-width / Unicode-line-terminator / C1
# terminal-escape primitives — byte-exact mirror of the set pinned in
# ``tests/test_sentinel_quota_status_trojan_source.py`` so any future
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
# do (the scrubber strips each code point at the ingestion boundary).
_UTF8_BYTES = {primitive: primitive.encode("utf-8") for primitive in _TROJAN_SOURCE_PRIMITIVES}


def _prepare_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str
) -> Path:
    """Pin ``_CACHE_DIR`` under ``tmp_path`` and return the events file path."""
    base = tmp_path / "cache-root"
    monkeypatch.setattr(cache, "_CACHE_DIR", base, raising=False)
    target = base / sanitize_filename(provider)
    target.mkdir(parents=True, exist_ok=True)
    return target / "events.json"


# ---------------------------------------------------------------------
# PoC 1: ``write_cache`` — the canonical RLO byte triplet MUST NOT
# leak into the on-disk file.
# ---------------------------------------------------------------------


def test_write_cache_does_not_emit_raw_bidi_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A planted feed item carrying U+202E reaches the on-disk
    ``cache/<provider>/events.json`` verbatim pre-fix. Operators
    viewing the file via ``cat`` / ``less`` / GitHub web UI / IDE
    preview see the BiDi-reversed display of the surrounding bytes,
    hiding the attack from review.
    """
    cache_file = _prepare_cache(tmp_path, monkeypatch, "wl")

    items = [
        {
            "title": "Westbahnhof‮moc.live",
            "guid": "wl-001",
            "source": "wl",
        }
    ]
    cache.write_cache("wl", items)

    raw_bytes = cache_file.read_bytes()
    # The smoking gun: U+202E encodes to the 3-byte UTF-8 sequence E2 80 AE.
    # Its appearance in the on-disk file means BiDi reversal is now active
    # for any cat / less / GitHub / IDE viewer of the file.
    assert b"\xe2\x80\xae" not in raw_bytes, (
        "U+202E (RLO) leaked verbatim into cache/<provider>/events.json — "
        "BiDi reversal is now active for any cat/less/GitHub viewer of the file."
    )


@pytest.mark.parametrize("primitive", _TROJAN_SOURCE_PRIMITIVES)
def test_write_cache_strips_every_trojan_source_primitive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, primitive: str
) -> None:
    """The scrubber's reject-set MUST be uniform across the canonical
    Trojan-Source / zero-width / line-terminator / C1 union — a single
    primitive surviving in raw form reopens the attack via a future
    title / description / GUID field carrying provider-fetched content.
    """
    cache_file = _prepare_cache(tmp_path, monkeypatch, f"poc-{ord(primitive):04x}")

    items = [
        {
            "title": f"evil{primitive}.example",
            "guid": f"id{primitive}-001",
            "source": "poc",
        }
    ]
    cache.write_cache(f"poc-{ord(primitive):04x}", items)

    raw_bytes = cache_file.read_bytes()
    raw_utf8 = _UTF8_BYTES[primitive]
    assert raw_utf8 not in raw_bytes, (
        f"Trojan-Source primitive U+{ord(primitive):04X} leaked into the "
        f"cache events file as raw UTF-8 bytes ({raw_utf8!r})."
    )


def test_write_cache_strips_primitive_in_dict_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dict KEYS are an attack surface too — a future schema-drift
    addition or operator mis-edit could plant the primitive in a key
    name. The scrubber MUST cover keys as well as values.
    """
    cache_file = _prepare_cache(tmp_path, monkeypatch, "key-poc")

    items = [
        {
            "title‮_evil": "harmless",
            "guid": "id-001",
            "source": "key-poc",
        }
    ]
    cache.write_cache("key-poc", items)

    raw_bytes = cache_file.read_bytes()
    assert b"\xe2\x80\xae" not in raw_bytes, (
        "U+202E leaked into a dict KEY of cache/<provider>/events.json — "
        "BiDi reversal is now active for any viewer of the file."
    )


def test_write_cache_strips_primitive_in_nested_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nested ``list[str]`` shapes (e.g. ``stations``, ``lines``,
    ``tags``) are common in feed-item schemas. The scrubber MUST
    recurse into them.
    """
    cache_file = _prepare_cache(tmp_path, monkeypatch, "nested-list")

    items = [
        {
            "title": "harmless",
            "guid": "id-001",
            "source": "nested-list",
            "stations": ["Westbahnhof", "Hauptbahnhof‮moc.live"],
        }
    ]
    cache.write_cache("nested-list", items)

    raw_bytes = cache_file.read_bytes()
    assert b"\xe2\x80\xae" not in raw_bytes, (
        "U+202E leaked from a nested list element into "
        "cache/<provider>/events.json."
    )


def test_write_cache_strips_primitive_in_nested_dict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nested ``dict`` shapes (e.g. ``metadata``, ``location``) are
    common in feed-item schemas. The scrubber MUST recurse into them.
    """
    cache_file = _prepare_cache(tmp_path, monkeypatch, "nested-dict")

    items = [
        {
            "title": "harmless",
            "guid": "id-001",
            "source": "nested-dict",
            "metadata": {
                "agency": "OEBB‮moc.live",
                "ref": "12345",
            },
        }
    ]
    cache.write_cache("nested-dict", items)

    raw_bytes = cache_file.read_bytes()
    assert b"\xe2\x80\xae" not in raw_bytes, (
        "U+202E leaked from a nested dict value into "
        "cache/<provider>/events.json."
    )


# ---------------------------------------------------------------------
# Regression: legitimate German content stays compact (no diff bloat).
# ---------------------------------------------------------------------


def test_write_cache_preserves_german_umlauts_as_raw_utf8(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fix-shape contract: ``ensure_ascii=False`` is preserved so
    legitimate German content (umlauts ä/ö/ü/Ä/Ö/Ü, sharp s ß) stays
    as raw UTF-8 in the on-disk file — every cache-update commit diff
    stays compact. A blanket ``ensure_ascii=True`` flip would balloon
    each German character to a 6-byte ``\\u00XX`` escape; the scrubber
    + flag-preservation contract avoids that bloat.
    """
    cache_file = _prepare_cache(tmp_path, monkeypatch, "german")

    items = [
        {
            "title": "Großbaustelle Wien Hütteldorf — Bahnübergang Mariä-Empfängnis",
            "guid": "g-001",
            "source": "german",
            "description": "Schienenersatzverkehr für Reisende — Öffnungszeiten!",
        }
    ]
    cache.write_cache("german", items)

    raw_bytes = cache_file.read_bytes()
    # Every German character MUST land as raw UTF-8, not the 6-byte
    # ``\\u00XX`` escape sequence.
    assert "ß".encode() in raw_bytes, "ß lost as raw UTF-8 — diff bloat!"
    assert "ü".encode() in raw_bytes, "ü lost as raw UTF-8 — diff bloat!"
    assert "ä".encode() in raw_bytes, "ä lost as raw UTF-8 — diff bloat!"
    assert "Ö".encode() in raw_bytes, "Ö lost as raw UTF-8 — diff bloat!"
    # Defence in depth: the literal ``ü`` escape (the bloated
    # form) MUST NOT appear in the on-disk file.
    decoded = raw_bytes.decode("utf-8")
    assert "\\u00fc" not in decoded, (
        "ü was escaped as \\u00fc — ensure_ascii=False contract broken, "
        "compact German diff lost."
    )
    assert "\\u00e4" not in decoded, (
        "ä was escaped as \\u00e4 — ensure_ascii=False contract broken."
    )


def test_write_cache_preserves_clean_payload_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: a clean payload (no Trojan-Source primitives)
    round-trips byte-stable through the scrubber. ``read_cache``
    recovers exactly the same dict every caller saw pre-fix.
    """
    cache_file = _prepare_cache(tmp_path, monkeypatch, "clean")

    items = [
        {
            "title": "Großbaustelle Hauptbahnhof",
            "guid": "clean-001",
            "source": "wl",
            "stations": ["Hauptbahnhof", "Praterstern"],
            "metadata": {"agency": "WL", "ref": "42"},
        }
    ]
    cache.write_cache("clean", items)

    parsed = json.loads(cache_file.read_text(encoding="utf-8"))
    assert parsed == items


# ---------------------------------------------------------------------
# Defence in depth: ``read_cache`` strips primitives from a poisoned
# on-disk cache file (planted before the fix, surviving from a
# corrupted previous run, or written by a future bypass).
# ---------------------------------------------------------------------


def test_read_cache_strips_pre_existing_trojan_source_primitives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pre-existing ``cache/<provider>/events.json`` carrying U+202E
    in raw UTF-8 form (planted before this fix or written by a future
    bypass of ``write_cache``'s scrubber) MUST NOT propagate the
    primitive into the in-memory data structure handed to the feed
    builder. ``read_cache`` strips the canonical attack-byte union
    on the way out.
    """
    cache_file = _prepare_cache(tmp_path, monkeypatch, "wl")
    # Plant the poisoned bytes directly — bypasses ``write_cache``.
    poisoned = json.dumps(
        [{"title": "Westbahnhof‮moc.live", "guid": "wl-001", "source": "wl"}],
        ensure_ascii=False,
    )
    cache_file.write_text(poisoned, encoding="utf-8")
    # Sanity: the planted file actually contains the dangerous bytes.
    assert b"\xe2\x80\xae" in cache_file.read_bytes()

    items = cache.read_cache("wl")

    assert isinstance(items, list)
    assert len(items) == 1
    title = items[0]["title"]
    assert "‮" not in title, (
        "read_cache returned a string still carrying U+202E — defence "
        "in depth at the read boundary failed."
    )


@pytest.mark.parametrize("primitive", _TROJAN_SOURCE_PRIMITIVES)
def test_read_cache_strips_every_trojan_source_primitive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, primitive: str
) -> None:
    """The defence-in-depth read-boundary scrubber MUST cover the same
    canonical union as the write-boundary scrubber.
    """
    cache_file = _prepare_cache(
        tmp_path, monkeypatch, f"read-{ord(primitive):04x}"
    )
    poisoned = json.dumps(
        [{"title": f"evil{primitive}.example", "guid": "x", "source": "x"}],
        ensure_ascii=False,
    )
    cache_file.write_text(poisoned, encoding="utf-8")

    items = cache.read_cache(f"read-{ord(primitive):04x}")

    assert len(items) == 1
    title = items[0]["title"]
    assert primitive not in title, (
        f"read_cache returned a string still carrying U+{ord(primitive):04X} "
        f"— read-boundary scrubber drift."
    )


def test_read_cache_preserves_german_umlauts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: the read-boundary scrubber MUST NOT touch
    legitimate German content. ä/ö/ü/ß round-trip identically.
    """
    cache_file = _prepare_cache(tmp_path, monkeypatch, "read-german")
    payload = [
        {
            "title": "Großbaustelle Hütteldorf",
            "guid": "g-1",
            "source": "wl",
            "description": "Schienenersatzverkehr für Reisende",
        }
    ]
    cache_file.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )

    items = cache.read_cache("read-german")

    assert items == payload
