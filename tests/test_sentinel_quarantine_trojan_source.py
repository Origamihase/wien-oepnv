"""Sentinel PoC: Trojan-Source / BiDi-Mark leakage at the auto-quarantine
writer boundary in ``scripts/update_all_stations.py``.

The 2026-05-10 ``feat(orchestrator): auto-quarantine failing stations
instead of hard-fail`` change (commit bca8065) introduced two new
operator-/public-facing sinks that interpolate raw upstream station
data:

  1. ``scripts/update_all_stations.py:_write_quarantine_file`` →
     ``data/quarantine.json``. The ``entry`` field of every quarantined
     station is serialised verbatim via
     ``json.dump(..., ensure_ascii=False, indent=2)``. ``ensure_ascii=False``
     emits non-ASCII code points as raw UTF-8 bytes — including the
     CVE-2021-42574 "Trojan Source" BiDi formatting controls
     (``U+202A-U+202E`` / ``U+2066-U+2069``), zero-width primitives
     (``U+200B-U+200F``), Unicode line / paragraph separators
     (``U+2028``-``U+2029``), the BOM (``U+FEFF``), and the C1 terminal-
     escape primitives (``\\x9b`` CSI / ``\\x9d`` OSC / ``\\x90`` DCS).
     An operator viewing ``data/quarantine.json`` via ``cat`` / ``less`` /
     editor preview / the GitHub web UI sees the BiDi-reversed display
     of the malicious station name — exactly the threat model the
     existing Trojan-Source RSS / stations-validator rounds closed for
     their respective sinks.

  2. ``scripts/update_all_stations.py:_render_diff_markdown`` →
     ``docs/stations_diff.md``. The diff section formatters
     interpolate the raw station name straight into the Markdown body
     (``f"- `{key}` — {name}"``). The diff report is committed to the
     repo by the cron pipeline (``update-cycle.yml``) and published via
     GitHub Pages, so a BiDi-marked station name attacks every public
     viewer of the diff. GitHub's Markdown renderer **does** honour
     ``U+202E`` in rendered text (the public Trojan-Source advisory is
     explicitly about GitHub's renderer), so the attack carries over
     untouched.

The shape mirrors the journal's BiDi-Mark Drift family (Rounds 2-8):
every new writer that targets either (a) a file format consumed by
human-eye review or (b) a Markdown sink rendered on GitHub MUST strip
or escape the canonical Trojan-Source / line-terminator / zero-width
union before interpolation. This PoC pins the rule for the
auto-quarantine writer + stations-diff renderer siblings.

Threat model
============

  1. Attacker compromises an upstream station provider (OEBB / VOR / WL
     cache poisoning, DNS-hijack of an unpinned upstream, malicious
     pull request against ``data/stations.json``).
  2. Attacker plants a station name carrying ``\\u202e`` (RIGHT-TO-LEFT
     OVERRIDE) such that the trailing characters render reversed —
     e.g. ``"Westbahnhof\\u202emoc.live"`` displays as
     ``Westbahnhofevil.com`` in any BiDi-honouring viewer.
  3. The validator's ``_find_security_issues`` flags the BiDi mark
     under ``_UNSAFE_CHARS_RE`` and routes the entry into the
     auto-quarantine bucket. The previously-safe ``stations.json`` is
     rewritten *without* the malicious entry — good.
  4. But the quarantine writer dumps the original ``entry`` dict via
     ``json.dump(..., ensure_ascii=False)``. ``U+202E`` survives as raw
     UTF-8 bytes in ``data/quarantine.json``.
  5. The diff writer separately observes that the malicious station
     used to be in ``before_snapshot`` (the unsanitised merged file)
     and is no longer in ``after_snapshot`` (the post-quarantine file),
     so the malicious name lands in ``diff["removed"]`` and flows into
     the Markdown body of ``docs/stations_diff.md``.
  6. An operator opens either artefact for review:
     * ``cat data/quarantine.json`` in a terminal → BiDi-reversed name
       visually hides the attack.
     * GitHub web UI rendering of ``docs/stations_diff.md`` →
       BiDi-reversed name in the public report page.

Fix shape
=========

  * ``_write_quarantine_file``: switch to ``ensure_ascii=True`` so every
    non-ASCII code point lands as a literal ``\\uXXXX`` escape — the
    BiDi controls cannot reach a renderer in their byte form. Forensic
    intent is preserved (the operator can read the escaped form and
    decode if desired), without exposing the raw bytes to any
    BiDi-honouring viewer.

  * ``_render_diff_markdown``: route every interpolated station name
    through ``src.utils.text.normalise_markdown_text`` (strips the
    canonical Trojan-Source / line-terminator / zero-width set defined
    in ``src/utils/text.py:_MARKDOWN_NORMALISE_UNSAFE_RE``) followed by
    ``escape_markdown`` for the human-readable text and
    ``safe_markdown_codespan`` for the identifier rendered inside the
    leading ``` `…` ``` span. Mirrors the canonical sanitiser pair the
    journal pinned for every Markdown sink in Rounds 2-3.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts import update_all_stations as wrapper


# Canonical Trojan-Source / zero-width / Unicode-line-terminator / C1
# terminal-escape primitives. Pinned against the
# ``src.utils.text._MARKDOWN_NORMALISE_UNSAFE_RE`` character class so a
# future widening of that regex (next BiDi-Mark Drift round) is mirrored
# here automatically by the sibling-regex-sync invariant.
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


def _malicious_station(
    bst_id: str | int,
    name: str,
    bst_code: str = "X",
    source: str = "vor",
) -> dict[str, Any]:
    return {
        "bst_id": bst_id,
        "bst_code": bst_code,
        "name": name,
        "source": source,
    }


# ---------------------------------------------------------------------
# PoC 1: ``_write_quarantine_file`` writes raw BiDi marks to disk.
# ---------------------------------------------------------------------


def test_quarantine_file_does_not_emit_raw_bidi_override(tmp_path: Path) -> None:
    """A station name carrying U+202E (RLO) lands in the quarantine
    output, but its raw UTF-8 byte sequence ``\\xe2\\x80\\xae`` MUST NOT
    appear in the on-disk file — otherwise ``cat`` / ``less`` / the
    GitHub web UI render the trailing characters reversed, hiding the
    attack from the reviewing operator.
    """
    malicious_name = "Westbahnhof‮moc.live"  # renders as Westbahnhofevil.com
    entry = _malicious_station(bst_id="2511", name=malicious_name)

    out_path = tmp_path / "quarantine.json"
    wrapper._write_quarantine_file(
        out_path,
        [entry],
        {"bst:2511 / code:X / source:vor": [
            {"category": "security", "reason": "Unsafe characters in name"}
        ]},
        "2026-05-10T12:00:00+00:00",
    )

    raw_bytes = out_path.read_bytes()
    # U+202E encodes to the 3-byte UTF-8 sequence E2 80 AE; its appearance
    # in the on-disk file is the Trojan-Source primitive's smoking gun.
    assert b"\xe2\x80\xae" not in raw_bytes, (
        "U+202E (RLO) leaked verbatim into quarantine.json — "
        "BiDi reversal is now active for any cat/less/GitHub viewer of the file."
    )
    # Defense-in-depth: the escaped form ``‮`` MUST appear so the
    # forensic intent (preserve what was quarantined) is not lost.
    assert "\\u202e" in raw_bytes.decode("utf-8"), (
        "The escaped sentinel ``\\u202e`` is missing — quarantine data lost."
    )


@pytest.mark.parametrize("primitive", _TROJAN_SOURCE_PRIMITIVES)
def test_quarantine_file_escapes_every_trojan_source_primitive(
    tmp_path: Path, primitive: str
) -> None:
    """The escape behaviour MUST be uniform across the canonical
    Trojan-Source / zero-width / line-terminator / C1 union — a single
    primitive surviving in raw form reopens the attack.
    """
    entry = _malicious_station(
        bst_id="42", name=f"Praterstern{primitive}injected", source="vor"
    )
    out_path = tmp_path / "quarantine.json"
    wrapper._write_quarantine_file(
        out_path,
        [entry],
        {},
        "2026-05-10T12:00:00+00:00",
    )

    raw_bytes = out_path.read_bytes()
    encoded = primitive.encode("utf-8")
    assert encoded not in raw_bytes, (
        f"Trojan-Source primitive U+{ord(primitive):04X} leaked verbatim "
        f"into quarantine.json as raw bytes {encoded!r}; this byte "
        f"sequence is what triggers terminal / GitHub rendering attacks."
    )


def test_quarantine_file_roundtrips_legit_ascii_payload_unchanged(
    tmp_path: Path,
) -> None:
    """Regression: ensure the hardening did not corrupt benign payloads.
    Operators must be able to ``json.loads`` the file and recover the
    quarantined entries byte-for-byte (post-decoding) for forensic review.
    """
    entry = _malicious_station(bst_id="7", name="Bad B", bst_code="B")
    out_path = tmp_path / "quarantine.json"
    wrapper._write_quarantine_file(
        out_path,
        [entry],
        {"bst:7 / code:B / source:vor": [
            {"category": "naming", "reason": "duplicate canonical name"}
        ]},
        "2026-05-10T12:00:00+00:00",
    )

    decoded = json.loads(out_path.read_text(encoding="utf-8"))
    assert decoded["count"] == 1
    assert decoded["stations"][0]["entry"] == entry


def test_quarantine_file_preserves_bidi_via_json_unicode_escape(
    tmp_path: Path,
) -> None:
    """Roundtrip: even after the byte-level sanitisation, ``json.loads``
    on the file recovers the ORIGINAL string (BiDi marks included).
    This proves the fix preserves forensic data via the literal escape
    sequence rather than discarding it.
    """
    malicious_name = "Westbahnhof‮moc.live"
    entry = _malicious_station(bst_id="2511", name=malicious_name)

    out_path = tmp_path / "quarantine.json"
    wrapper._write_quarantine_file(
        out_path,
        [entry],
        {},
        "2026-05-10T12:00:00+00:00",
    )

    decoded = json.loads(out_path.read_text(encoding="utf-8"))
    recovered_name = decoded["stations"][0]["entry"]["name"]
    assert recovered_name == malicious_name, (
        "After JSON decoding, the original BiDi-laden string must be "
        "recoverable — the fix MUST escape the bytes on the wire, not "
        "strip them from the in-memory data."
    )


# ---------------------------------------------------------------------
# PoC 2: ``_render_diff_markdown`` interpolates raw BiDi marks into the
# committed-and-published ``docs/stations_diff.md`` body.
# ---------------------------------------------------------------------


def test_diff_markdown_strips_bidi_override_from_removed_name() -> None:
    """A station that was removed during auto-quarantine carries its
    pre-quarantine BiDi-laden name into ``diff["removed"]`` (the
    snapshot taken before the quarantine writeout). The rendered
    markdown is then committed and rendered on GitHub Pages — the
    BiDi mark MUST be stripped before reaching the published file.
    """
    malicious_name = "Westbahnhof‮moc.live"
    diff: wrapper._DiffResult = {
        "added": [],
        "removed": [("bst:2511", malicious_name)],
        "renamed": [],
        "coord_shifted": [],
    }

    rendered = wrapper._render_diff_markdown(
        diff, before_count=2, after_count=1, timestamp="2026-05-10T12:00:00+00:00"
    )

    assert "‮" not in rendered, (
        "RLO BiDi-mark survived ``_render_diff_markdown`` and now "
        "renders reversed in docs/stations_diff.md on GitHub Pages — "
        "every public viewer sees the attacked text."
    )


def test_diff_markdown_strips_bidi_from_added_renamed_coord_shift() -> None:
    """Every section's name interpolation is a sink — verify the strip
    is applied uniformly so no upstream-controlled name reaches the
    published file unfiltered.
    """
    malicious = "Praterstern‮moc.live"
    diff: wrapper._DiffResult = {
        "added": [("bst:1", malicious)],
        "removed": [("bst:2", malicious)],
        "renamed": [("bst:3", malicious, "Other")],
        "coord_shifted": [("bst:4", malicious, 1200)],
    }

    rendered = wrapper._render_diff_markdown(
        diff, before_count=4, after_count=4, timestamp="2026-05-10T12:00:00+00:00"
    )

    assert "‮" not in rendered, (
        "BiDi override leaked through one of the diff section formatters."
    )


@pytest.mark.parametrize("primitive", _TROJAN_SOURCE_PRIMITIVES)
def test_diff_markdown_strips_every_trojan_source_primitive(primitive: str) -> None:
    """Mirror the canonical Trojan-Source / zero-width / line-terminator /
    C1 union; the renderer MUST not let any primitive through to the
    published markdown body.
    """
    name = f"Hbf{primitive}injected"
    diff: wrapper._DiffResult = {
        "added": [],
        "removed": [("bst:99", name)],
        "renamed": [],
        "coord_shifted": [],
    }

    rendered = wrapper._render_diff_markdown(
        diff, before_count=99, after_count=98, timestamp="2026-05-10T12:00:00+00:00"
    )

    assert primitive not in rendered, (
        f"Primitive U+{ord(primitive):04X} survived the diff renderer "
        f"and now lands in the committed/published markdown body."
    )


def test_diff_markdown_strips_bidi_from_renamed_after_name() -> None:
    """The ``Renamed`` formatter has TWO sinks per row — the before-name
    and the after-name. Both must be sanitised; a fix that strips only
    one side reopens the attack via the unprotected sink.
    """
    malicious_before = "Wien Westbf"
    malicious_after = "Wien Westbahnhof‮moc.live"
    diff: wrapper._DiffResult = {
        "added": [],
        "removed": [],
        "renamed": [("bst:2511", malicious_before, malicious_after)],
        "coord_shifted": [],
    }

    rendered = wrapper._render_diff_markdown(
        diff, before_count=1, after_count=1, timestamp="2026-05-10T12:00:00+00:00"
    )

    assert "‮" not in rendered, (
        "RLO BiDi-mark in renamed-to name leaked into the diff markdown."
    )


def test_diff_markdown_strips_bidi_from_key_codespan() -> None:
    """The diff key is rendered inside a Markdown code span
    (`` `<key>` ``). A name-based key (``name:<raw name>``) can carry
    BiDi marks as well — the codespan boundary must be sanitised too.
    """
    # Name-keyed entry (no bst_id) — the ``name:<raw name>`` form lands
    # the BiDi mark inside the leading codespan.
    diff: wrapper._DiffResult = {
        "added": [],
        "removed": [("name:Hbf‮moc.live", "Hbf‮moc.live")],
        "renamed": [],
        "coord_shifted": [],
    }

    rendered = wrapper._render_diff_markdown(
        diff, before_count=1, after_count=0, timestamp="2026-05-10T12:00:00+00:00"
    )

    assert "‮" not in rendered, (
        "RLO in the ``name:<>`` codespan leaked through ``_render_diff_markdown`` — "
        "GitHub renders code-span contents with BiDi rules applied."
    )


def test_diff_markdown_preserves_legitimate_unicode_names() -> None:
    """Regression: legitimate non-ASCII station names (umlauts,
    diacritics, Eastern European characters) must round-trip unchanged.
    The sanitiser strips only the Trojan-Source / zero-width / control
    union — NOT broad classes of "non-ASCII".
    """
    diff: wrapper._DiffResult = {
        "added": [("bst:1", "Wien Floridsdorf")],
        "removed": [("bst:2", "München Hbf")],
        "renamed": [("bst:3", "Süd", "Südtirol")],
        "coord_shifted": [("bst:4", "Wien Praterstern", 150)],
    }

    rendered = wrapper._render_diff_markdown(
        diff, before_count=4, after_count=4, timestamp="2026-05-10T12:00:00+00:00"
    )

    # Each legitimate name must survive (after Markdown-escape, e.g. of
    # the surrounding punctuation, the characters themselves persist).
    assert "Floridsdorf" in rendered
    assert "München" in rendered
    assert "Südtirol" in rendered
    assert "Praterstern" in rendered
