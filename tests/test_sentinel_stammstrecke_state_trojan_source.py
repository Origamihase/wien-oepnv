"""Sentinel PoC: Trojan-Source / BiDi-Mark leakage at the two committed
operator-facing JSON sidecar writers in
``scripts/update_stammstrecke_status.py`` —
``_save_pending_trips`` (``cache/stammstrecke/pending_trips.json``) and
``_save_recently_finalised`` (``cache/stammstrecke/recently_finalised.json``).

Both files are committed to ``main`` by the IFTTT-triggered
``update-cycle.yml`` (Stammstrecke step) — the workflow's auto-commit
step uses ``add_options: '-A'`` so every modified file under ``cache/``
is staged and pushed. The cache files are operator-facing artefacts —
``cat`` / ``less`` / the GitHub web UI / IDE preview all honour BiDi
formatting controls when displaying the file.

Pre-fix both writers serialised the payload with
``json.dump(..., ensure_ascii=False, ...)``. ``ensure_ascii=False``
emits every non-ASCII code point as raw UTF-8 — including the
CVE-2021-42574 "Trojan Source" BiDi formatting controls
(``U+202A-U+202E`` / ``U+2066-U+2069``), zero-width primitives
(``U+200B-U+200F``), Unicode line / paragraph separators
(``U+2028``-``U+2029``), the BOM (``U+FEFF``), and the C1 terminal-
escape primitives (``\\x9b`` CSI, ``\\x9d`` OSC, ``\\x90`` DCS).

The other state-sink JSON writers in the project family
(``_save_state`` in ``data/first_seen.json``,
``_write_heartbeat_file`` in ``data/stations_last_run.json``,
``_write_request_count_file`` in ``data/vor_request_count.json``,
``MonthlyQuota.save_atomic`` in ``data/places_quota.json``,
``write_status`` in ``cache/<provider>/last_run.json``) all switched
to ``ensure_ascii=True`` in earlier rounds (PRs #1434 / #1435 / Round
10 / Round 11 of the BiDi-Mark Drift series). The two stammstrecke
sidecar writers below were the deferred siblings that never got the
canonical defence applied — none of the prior rounds named these
specific files.

Attack path
============

  1. The VAO ``/trip`` upstream is compromised (or returns malformed
     data, or is MITM-attacked, or a future API change ships a
     line-name field that carries non-ASCII formatting marks). The
     ``leg.name`` field arrives carrying ``\\u202e`` (RIGHT-TO-LEFT
     OVERRIDE) — e.g. ``"S\\u202e2evil"`` displays as ``"Slive2"``
     reversed in any BiDi-honouring viewer.
  2. ``scripts/update_stammstrecke_status.py:_collect_sbahn_leg_observations``
     extracts the leg name via ``_canonical_line_name(leg.get("name") or "")``.
     The canonicaliser strips only whitespace runs (``\\s+``) and the
     ``|`` separator, then uppercases. ``\\u202e`` is in Unicode
     category ``Cf`` (Format), NOT ``Zs`` (Whitespace), so ``\\s``
     does NOT match it — the primitive survives canonicalisation
     verbatim.
  3. The leg passes ``_is_sbahn_leg`` because the ``category`` field
     ("S"/"R"/"REX") is the primary signal — the name regex is only a
     fallback. So a malicious name with a primitive can still ship.
  4. The observation flows to ``_observe_legs`` which builds a
     ``_PendingTrip`` with ``name="S\\u202e2evil"`` and inserts it
     into the ``state`` dict keyed by ``_identity_key`` —
     ``f"{direction}|{name}|{scheduled.isoformat()}"`` — so the
     primitive lands in BOTH the dict KEY and the inner ``name`` field.
  5. ``_save_pending_trips`` writes the dict to
     ``cache/stammstrecke/pending_trips.json`` with ``ensure_ascii=False``.
     The BiDi mark survives as raw UTF-8 bytes (``\\xe2\\x80\\xae``).
  6. ``_finalize_departed`` later moves the same identity key into
     the ``recently_finalised`` companion mapping. ``_save_recently_finalised``
     writes that to ``cache/stammstrecke/recently_finalised.json`` with
     ``ensure_ascii=False`` — the primitive still leaks.
  7. The ``update-cycle.yml`` auto-commit (``add_options: '-A'``)
     stages both cache files and pushes them to ``main``.
  8. Operator opens the file via ``cat``, ``less``, ``git diff``,
     GitHub web UI, or IDE preview → the BiDi-reversed display of the
     planted name hides the attack from the reviewing operator. A
     subsequent operator-supplied diagnostic command interpolating the
     name into stderr / a downstream tool / a copied error message
     re-flows the primitive into the next consumer.

Fix shape
==========

Both writers: switch from ``ensure_ascii=False`` to ``ensure_ascii=True``.

  * Forensic intent is preserved (``json.loads`` recovers the original
    string from the literal ``\\uXXXX`` escape, so the load-modify-save
    round-trip is byte-equivalent at the parsed-Python level).
  * No raw BiDi / line-separator / C1 byte reaches any byte viewer
    (``cat`` / ``less`` / GitHub web UI / IDE preview).
  * Mirrors the canonical fix shape pinned in PR #1434 for
    ``_write_quarantine_file``, PR #1435 for ``_save_state``, and the
    Round 10 / Round 11 closures so the closing checklist's invariant
    is now uniform across every committed operator-facing JSON state
    sink in the project.
  * Legitimate content in these files is exclusively ASCII — direction
    labels are hardcoded ``"Meidling"`` / ``"Floridsdorf"``, line
    names are short S-Bahn / R / REX designations
    (``S1`` / ``S80`` / ``R8`` / ``REX3``), timestamps are ISO-8601 —
    so the on-disk-byte change is invisible for the happy path.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import update_stammstrecke_status as script  # noqa: E402


VIENNA_TZ = script.VIENNA_TZ


# Canonical Trojan-Source / zero-width / Unicode-line-terminator / C1
# terminal-escape primitives — byte-exact mirror of the set pinned in
# ``tests/test_sentinel_state_heartbeat_trojan_source.py`` so any
# future widening of the canonical floor is enforced uniformly across
# the committed-sidecar writer family.
_TROJAN_SOURCE_PRIMITIVES: tuple[str, ...] = (
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


# UTF-8 byte sequence each primitive encodes to. Pre-fix every byte
# sequence appears in the on-disk file verbatim; post-fix none of them
# do (the JSON encoder replaces each code point with the literal
# ``\\uXXXX`` escape).
_UTF8_BYTES: dict[str, bytes] = {
    primitive: primitive.encode("utf-8") for primitive in _TROJAN_SOURCE_PRIMITIVES
}


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _make_pending(
    *,
    direction: str = "Meidling",
    name: str = "S1",
    scheduled: datetime | None = None,
    latest_delay_minutes: float = 0.0,
    last_seen_at: datetime | None = None,
) -> script._PendingTrip:
    """Build a ``_PendingTrip`` with deterministic Vienna timestamps."""
    base = datetime(2026, 5, 13, 8, 0, tzinfo=VIENNA_TZ)
    return script._PendingTrip(
        direction=direction,
        name=name,
        scheduled=scheduled or base,
        latest_delay_minutes=latest_delay_minutes,
        last_seen_at=last_seen_at or base,
    )


@pytest.fixture
def _stammstrecke_state_dir(tmp_path: Path) -> Iterator[Path]:
    """Provide a writable temp directory for the two state sidecar files."""
    yield tmp_path


# ---------------------------------------------------------------------
# PoC 1: ``_save_pending_trips`` writes raw BiDi marks in the inner
# ``name`` field AND in the dict key (built via ``_identity_key``).
# ---------------------------------------------------------------------


def test_save_pending_trips_does_not_emit_raw_bidi_override_in_name(
    _stammstrecke_state_dir: Path,
) -> None:
    """A leg name carrying U+202E (RLO) reaches ``_PendingTrip.name``
    verbatim because ``_canonical_line_name`` only strips ``\\s+`` and
    ``|`` (U+202E is in category ``Cf``, not ``Zs`` / not ``\\s``). The
    pre-fix writer used ``ensure_ascii=False`` which emits the raw UTF-8
    byte sequence ``\\xe2\\x80\\xae`` to disk — a downstream operator
    viewing ``cache/stammstrecke/pending_trips.json`` via ``cat`` /
    ``less`` / GitHub web UI / IDE preview sees the BiDi-reversed
    display, hiding the attack from review.
    """
    state = {
        script._identity_key(
            "Meidling",
            "S‮2evil",
            datetime(2026, 5, 13, 8, 0, tzinfo=VIENNA_TZ),
        ): _make_pending(
            name="S‮2evil",
            latest_delay_minutes=4.5,
        ),
    }
    out_path = _stammstrecke_state_dir / "pending_trips.json"
    assert script._save_pending_trips(out_path, state) is True

    raw_bytes = out_path.read_bytes()

    # The smoking gun: U+202E encodes to the 3-byte UTF-8 sequence E2 80 AE.
    # Its appearance in the on-disk file means BiDi reversal is now active
    # for any cat / less / GitHub / IDE viewer of the file.
    assert b"\xe2\x80\xae" not in raw_bytes, (
        "U+202E (RLO) leaked verbatim into cache/stammstrecke/pending_trips.json "
        "— BiDi reversal is now active for any cat/less/GitHub viewer of the file."
    )
    # Defense-in-depth: the escaped form ``\\u202e`` MUST appear so the
    # forensic intent (preserve which trip was tracked) is not lost.
    assert "\\u202e" in raw_bytes.decode("utf-8"), (
        "The escaped sentinel ``\\u202e`` is missing — pending-trip data lost."
    )


@pytest.mark.parametrize("primitive", _TROJAN_SOURCE_PRIMITIVES)
def test_save_pending_trips_escapes_every_trojan_source_primitive_in_name(
    _stammstrecke_state_dir: Path, primitive: str
) -> None:
    """The escape behaviour MUST be uniform across the canonical
    Trojan-Source / zero-width / line-terminator / C1 union — a single
    primitive surviving in raw form reopens the attack via the same
    ``leg.name`` → canonicaliser → ``_PendingTrip.name`` →
    ``cache/stammstrecke/pending_trips.json`` path.
    """
    name = f"S{primitive}2"
    state = {
        script._identity_key(
            "Floridsdorf",
            name,
            datetime(2026, 5, 13, 9, 0, tzinfo=VIENNA_TZ),
        ): _make_pending(
            direction="Floridsdorf",
            name=name,
            latest_delay_minutes=1.0,
        ),
    }
    out_path = _stammstrecke_state_dir / "pending_trips.json"
    assert script._save_pending_trips(out_path, state) is True

    raw_bytes = out_path.read_bytes()

    raw_utf8 = _UTF8_BYTES[primitive]
    assert raw_utf8 not in raw_bytes, (
        f"Trojan-Source primitive U+{ord(primitive):04X} leaked into "
        "cache/stammstrecke/pending_trips.json as raw UTF-8 bytes "
        f"({raw_utf8!r})."
    )


@pytest.mark.parametrize("primitive", _TROJAN_SOURCE_PRIMITIVES)
def test_save_pending_trips_escapes_every_trojan_source_primitive_in_key(
    _stammstrecke_state_dir: Path, primitive: str
) -> None:
    """The dict-KEY path: ``_identity_key`` interpolates ``name``
    verbatim into ``f"{direction}|{name}|{scheduled.isoformat()}"``,
    so the primitive lands in the JSON top-level key (not just the
    inner ``name`` value). The escape behaviour MUST cover the key
    too — JSON dict keys are emitted via the same encoder so
    ``ensure_ascii=True`` covers them.
    """
    name = f"R{primitive}99"
    state = {
        script._identity_key(
            "Meidling",
            name,
            datetime(2026, 5, 13, 7, 30, tzinfo=VIENNA_TZ),
        ): _make_pending(
            name=name,
            latest_delay_minutes=2.0,
        ),
    }
    out_path = _stammstrecke_state_dir / "pending_trips.json"
    assert script._save_pending_trips(out_path, state) is True

    raw_bytes = out_path.read_bytes()
    raw_utf8 = _UTF8_BYTES[primitive]
    assert raw_utf8 not in raw_bytes, (
        f"Trojan-Source primitive U+{ord(primitive):04X} leaked into the "
        "pending_trips.json dict key as raw UTF-8 bytes "
        f"({raw_utf8!r})."
    )


def test_save_pending_trips_round_trips_through_load_after_escape(
    _stammstrecke_state_dir: Path,
) -> None:
    """Forensic-intent regression: the load-modify-save cycle must remain
    byte-equivalent at the parsed-Python level after the
    ``ensure_ascii=True`` switch. ``json.loads`` recovers the original
    string from the literal ``\\uXXXX`` escape so the in-memory state
    survives a write-then-read cycle.
    """
    name = "S‮2"
    state_in = {
        script._identity_key(
            "Meidling",
            name,
            datetime(2026, 5, 13, 8, 0, tzinfo=VIENNA_TZ),
        ): _make_pending(
            name=name,
            latest_delay_minutes=4.5,
        ),
    }
    out_path = _stammstrecke_state_dir / "pending_trips.json"
    assert script._save_pending_trips(out_path, state_in) is True
    state_out = script._load_pending_trips(out_path)
    # The loader re-canonicalises the name — but the canonicaliser does
    # not strip BiDi marks, so the recovered name still equals the
    # original.
    only_key = next(iter(state_in))
    assert only_key in state_out, "Identity key was not recovered byte-for-byte."
    assert state_out[only_key].name == name, "Trip name lost across round-trip."
    assert state_out[only_key].latest_delay_minutes == 4.5


# ---------------------------------------------------------------------
# PoC 2: ``_save_recently_finalised`` writes raw BiDi marks in the dict
# KEY (the same identity-key shape that carries leg-name primitives).
# ---------------------------------------------------------------------


def test_save_recently_finalised_does_not_emit_raw_bidi_override_in_key(
    _stammstrecke_state_dir: Path,
) -> None:
    """The companion ledger keyed by the same ``_identity_key`` shape:
    when the upstream-controlled leg name flows into the key via
    ``_finalize_departed`` -> ``recently_finalised[key] = now``, the
    pre-fix writer leaks the BiDi mark to disk just like
    ``_save_pending_trips`` does.
    """
    key = script._identity_key(
        "Meidling",
        "S‮2evil",
        datetime(2026, 5, 13, 8, 0, tzinfo=VIENNA_TZ),
    )
    finalised = {key: datetime(2026, 5, 13, 8, 5, tzinfo=VIENNA_TZ)}

    out_path = _stammstrecke_state_dir / "recently_finalised.json"
    assert script._save_recently_finalised(out_path, finalised) is True

    raw_bytes = out_path.read_bytes()
    assert b"\xe2\x80\xae" not in raw_bytes, (
        "U+202E (RLO) leaked verbatim into "
        "cache/stammstrecke/recently_finalised.json — BiDi reversal "
        "is now active for any cat/less/GitHub viewer of the file."
    )
    assert "\\u202e" in raw_bytes.decode("utf-8"), (
        "The escaped sentinel ``\\u202e`` is missing — finalised key data lost."
    )


@pytest.mark.parametrize("primitive", _TROJAN_SOURCE_PRIMITIVES)
def test_save_recently_finalised_escapes_every_trojan_source_primitive(
    _stammstrecke_state_dir: Path, primitive: str
) -> None:
    """Uniform coverage of the canonical Trojan-Source / zero-width /
    line-terminator / C1 union for the recently-finalised companion
    ledger writer.
    """
    key = script._identity_key(
        "Floridsdorf",
        f"REX{primitive}3",
        datetime(2026, 5, 13, 9, 30, tzinfo=VIENNA_TZ),
    )
    finalised = {key: datetime(2026, 5, 13, 9, 35, tzinfo=VIENNA_TZ)}

    out_path = _stammstrecke_state_dir / "recently_finalised.json"
    assert script._save_recently_finalised(out_path, finalised) is True

    raw_bytes = out_path.read_bytes()
    raw_utf8 = _UTF8_BYTES[primitive]
    assert raw_utf8 not in raw_bytes, (
        f"Trojan-Source primitive U+{ord(primitive):04X} leaked into "
        "cache/stammstrecke/recently_finalised.json as raw UTF-8 bytes "
        f"({raw_utf8!r})."
    )


def test_save_recently_finalised_round_trips_through_load_after_escape(
    _stammstrecke_state_dir: Path,
) -> None:
    """Forensic-intent regression for the companion ledger: the
    load-modify-save cycle stays byte-equivalent at the parsed-Python
    level after the ``ensure_ascii=True`` switch.
    """
    key = script._identity_key(
        "Meidling",
        "S‮2",
        datetime(2026, 5, 13, 8, 0, tzinfo=VIENNA_TZ),
    )
    finalised_in = {key: datetime(2026, 5, 13, 8, 5, tzinfo=VIENNA_TZ)}

    out_path = _stammstrecke_state_dir / "recently_finalised.json"
    assert script._save_recently_finalised(out_path, finalised_in) is True
    finalised_out = script._load_recently_finalised(out_path)
    assert key in finalised_out, "Identity key was not recovered byte-for-byte."
    assert finalised_out[key] == datetime(
        2026, 5, 13, 8, 5, tzinfo=VIENNA_TZ
    ), "Finalised timestamp lost across round-trip."


# ---------------------------------------------------------------------
# Additive invariants: the canonical name normaliser does NOT strip
# BiDi / format-class characters, so the upstream defense-in-depth
# layer cannot be relied on.
# ---------------------------------------------------------------------


def test_canonical_line_name_does_not_strip_bidi_override() -> None:
    """``_canonical_line_name`` strips only ``\\s+`` (whitespace per
    the Python ``re`` engine) and the ``|`` separator. U+202E is in
    Unicode category ``Cf`` (Format) — it is NOT in ``\\s``. This
    invariant pins the canonicaliser's narrow scope so any future
    audit knows the writer-side defense (``ensure_ascii=True``) is
    the load-bearing one.
    """
    canon = script._canonical_line_name("S‮2")
    assert canon == "S‮2", (
        "_canonical_line_name unexpectedly stripped U+202E — the "
        "writer-side defense is no longer the only barrier."
    )


# Python's ``\\s`` regex matches U+2028 LINE SEPARATOR and U+2029
# PARAGRAPH SEPARATOR (they are Unicode-whitespace per Python's
# regex engine). ``_canonical_line_name`` strips ``\\s+``, so those
# two specific primitives get incidentally removed by the
# canonicaliser. Every OTHER primitive in the canonical attack
# union survives canonicalisation verbatim — for those, the
# writer-side ``ensure_ascii=True`` barrier is the only defence.
_PRIMITIVES_NOT_STRIPPED_BY_CANONICAL = tuple(
    p for p in _TROJAN_SOURCE_PRIMITIVES if p not in {" ", " "}
)


@pytest.mark.parametrize("primitive", _PRIMITIVES_NOT_STRIPPED_BY_CANONICAL)
def test_canonical_line_name_does_not_strip_any_trojan_source_primitive(
    primitive: str,
) -> None:
    """Uniform coverage for the negative invariant: every Trojan-Source
    primitive in the canonical attack union (other than U+2028 / U+2029
    which Python's ``\\s`` engine incidentally strips) survives
    canonicalisation, so the writer-side ``ensure_ascii=True`` barrier
    is the only barrier for them.
    """
    canon = script._canonical_line_name(f"S{primitive}1")
    # The primitive must be present in the canonicalised output so
    # the writer-side test is exercising the non-trivial case.
    assert primitive in canon, (
        f"Primitive U+{ord(primitive):04X} was stripped by "
        "_canonical_line_name — the test now validates a no-op."
    )


def test_save_pending_trips_preserves_legitimate_ascii_state(
    _stammstrecke_state_dir: Path,
) -> None:
    """Regression: the happy-path payload (ASCII direction +
    short-form line name + ISO timestamps) is byte-stable through
    the writer.
    """
    state = {
        script._identity_key(
            "Meidling",
            "S1",
            datetime(2026, 5, 13, 8, 0, tzinfo=VIENNA_TZ),
        ): _make_pending(name="S1", latest_delay_minutes=4.5),
        script._identity_key(
            "Floridsdorf",
            "REX3",
            datetime(2026, 5, 13, 8, 15, tzinfo=VIENNA_TZ),
        ): _make_pending(
            direction="Floridsdorf",
            name="REX3",
            latest_delay_minutes=2.0,
        ),
    }
    out_path = _stammstrecke_state_dir / "pending_trips.json"
    assert script._save_pending_trips(out_path, state) is True
    parsed = json.loads(out_path.read_text(encoding="utf-8"))
    # Both identity keys round-trip through the parser unchanged.
    assert set(parsed) == set(state)
    # Every payload field is recoverable.
    for key, trip in state.items():
        assert parsed[key]["direction"] == trip.direction
        assert parsed[key]["name"] == trip.name
        assert parsed[key]["latest_delay_minutes"] == trip.latest_delay_minutes
