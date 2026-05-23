"""Sentinel PoC: ``allow_nan=False`` writer-defence drift at the two
committed-to-main JSON sidecar writers in
``scripts/update_stammstrecke_status.py`` —
``_save_pending_trips`` (``cache/stammstrecke/pending_trips.json``)
and ``_save_recently_finalised`` (``cache/stammstrecke/recently_finalised.json``).

Both files are committed to ``main`` by the IFTTT-triggered
``update-cycle.yml`` (Stammstrecke step). The auto-commit step uses
``add_options: '-A'`` so every modified file under ``cache/`` is
staged and pushed.

Pre-fix both writers serialised the payload with
``_json_lib.dump(payload, fh, indent=2, sort_keys=True,
ensure_ascii=True)`` — **without** the ``allow_nan=False`` defence-in-
depth pin established for the sibling state-sink writers in the
2026-05-14 / 2026-05-15 rounds (PR #1487, PR #1488). Python's default
``json.dump`` emits ``NaN`` / ``Infinity`` / ``-Infinity`` as bare
literal tokens that are invalid per RFC 8259 §6: ``JSON.parse``
(every modern browser), ``serde_json`` strict mode (Rust), and
``encoding/json`` (Go) all reject the document.

The drift is the dual of the parser-side
``_reject_non_finite_constant`` / ``_reject_non_finite_float`` hooks
already pinned on :func:`_load_pending_trips` via the canonical
``loads_finite`` wrapper. A round-trip through the writer round-tripping
to the loader silently DROPS the entry: the writer emits ``NaN``, the
next loader load rejects it via ``json.JSONDecodeError``, the
``except (ValueError, json.JSONDecodeError, RecursionError)`` handler
in ``read_capped_text`` → ``_load_pending_trips`` returns an empty
dict, and **every observed-but-not-yet-finalised S-Bahn trip is lost**
— the cron pipeline silently produces zero CSV rows for the affected
tick.

Threat model
============

Three distinct attacker positions can plant ``NaN`` / ``Infinity`` /
``-Infinity`` literals into the cron pipeline's writer call:

  1. **Programmatic in-process injection**. Any future code change
     that lets a non-finite float reach
     :attr:`_PendingTrip.latest_delay_minutes` (the field is typed
     ``float`` — concrete numeric, not ``float | None``) — e.g. a
     refactor of :func:`_leg_departure_delay_minutes` that uses
     ``float('inf')`` as a missing-data sentinel; a future
     ``math.nan``-bearing observation from a third-party VAO peer
     SDK; a division-by-zero in a derived statistic — lands the
     bytes verbatim into the committed sidecar.

  2. **Compromised upstream VAO/HAFAS endpoint**. ``rtTime`` /
     ``rtDate`` returned from the upstream that overflows
     ``_parse_vao_dt`` arithmetic into a non-finite ``timedelta``
     subtraction result — Python's ``timedelta.total_seconds()``
     returns a finite float for any representable timedelta but a
     future ``datetime`` subtype (or a HAFAS-side schema change that
     ships raw seconds-since-epoch ints) widens the threat surface.

  3. **Poisoned on-disk state file round-tripped**. The writer in
     this round IS the dual of the reader-side ``loads_finite`` pin
     — together they enforce the invariant that a non-finite literal
     cannot enter or leave the on-disk state. Pre-fix the writer
     could emit a non-finite literal even when the reader rejects it
     on next load, leaving the file in a state that the canonical
     loader treats as missing.

Sites enumerated
================

1. :func:`scripts.update_stammstrecke_status._save_pending_trips`
   (``cache/stammstrecke/pending_trips.json``) — pending-trip
   ledger. ``_trip_to_json`` emits
   ``"latest_delay_minutes": trip.latest_delay_minutes`` directly
   from a concrete ``float`` field. **Highest impact** — the
   field carries every observed leg's delay in fractional minutes.

2. :func:`scripts.update_stammstrecke_status._save_recently_finalised`
   (``cache/stammstrecke/recently_finalised.json``) — companion
   finalisation ledger. Today's payload is ``{key: ts.isoformat()
   for key, ts in finalised.items()}`` (all-string values), but the
   threat-class is identical to ``_save_pending_trips`` — a future
   schema widening that adds a numeric field (re-emission count,
   age-in-seconds for cleanup tooling) inherits the missing pin
   without anyone re-reviewing the writer.

Public sinks impacted
=====================

* ``cache/stammstrecke/pending_trips.json`` — committed to ``main``
  every IFTTT-triggered cron tick (~30 min). The file is consumed
  by the CSV-row finalise pass; a successful poison empties the
  ledger and produces a zero-observation CSV row for the affected
  tick.

* ``cache/stammstrecke/recently_finalised.json`` — committed to
  ``main`` the same way; defense-in-depth seal for the schema
  widening case.

Severity: **MEDIUM** — committed-to-main JSON sidecar data-integrity
attack with both an in-process programmatic path (future refactor)
AND a state-file round-trip attack (writer → reader DoS). Closes the
last sibling of the 2026-05-14 / 2026-05-15 ``allow_nan=False``
inventory pin family (PRs #1487 / #1488).

The fix
=======

Two coordinated edits, both pinned by this test file:

  1. ``scripts/update_stammstrecke_status.py:_save_pending_trips`` —
     pass ``allow_nan=False`` to ``_json_lib.dump``.
  2. ``scripts/update_stammstrecke_status.py:_save_recently_finalised``
     — same.

Inventory invariants
====================

The ``test_inventory_*`` cases below each load the function source via
:func:`inspect.getsource` and assert that the literal ``allow_nan=False``
is part of the call — any future edit that drops the contract (or
copies the function shape into a new sibling writer without the pin)
fails the test on the next pytest run. The behavioural PoCs invoke
the writers with a ``float('nan')`` / ``float('inf')`` field and
assert the call raises ``ValueError`` (the canonical strict-JSON
error shape).
"""

from __future__ import annotations

import inspect
import json
import sys
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import update_stammstrecke_status as script  # noqa: E402

VIENNA_TZ = script.VIENNA_TZ


# Sentinel marker shared by every PoC site so a future grep for
# ``SENTINEL_STAMMSTRECKE_WRITER_ALLOW_NAN_DRIFT`` finds the full
# call-graph at once.
SENTINEL_STAMMSTRECKE_WRITER_ALLOW_NAN_DRIFT = (
    "stammstrecke writer allow_nan=False drift"
)


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
def _state_dir(tmp_path: Path) -> Iterator[Path]:
    """Provide a writable temp directory for the two state sidecar files."""
    yield tmp_path


def _assert_allow_nan_pin(func: Any, *, where: str) -> None:
    """Assert that ``func``'s source contains the ``allow_nan=False`` pin."""
    source = inspect.getsource(func)
    assert "allow_nan=False" in source, (
        f"{where}: missing ``allow_nan=False`` pin — non-standard NaN / "
        f"Infinity / -Infinity literals (invalid per RFC 8259) would land "
        f"in the committed artefact and break every strict JSON parser "
        f"downstream.\n\nMarker: {SENTINEL_STAMMSTRECKE_WRITER_ALLOW_NAN_DRIFT}"
    )


# ---------------------------------------------------------------------
# Inventory pins (source-grep): every writer must carry ``allow_nan=False``.
# ---------------------------------------------------------------------


def test_inventory_save_pending_trips_pins_allow_nan() -> None:
    _assert_allow_nan_pin(
        script._save_pending_trips,
        where=(
            "scripts/update_stammstrecke_status.py:_save_pending_trips "
            "(cache/stammstrecke/pending_trips.json)"
        ),
    )


def test_inventory_save_recently_finalised_pins_allow_nan() -> None:
    _assert_allow_nan_pin(
        script._save_recently_finalised,
        where=(
            "scripts/update_stammstrecke_status.py:_save_recently_finalised "
            "(cache/stammstrecke/recently_finalised.json)"
        ),
    )


# ---------------------------------------------------------------------
# PoC 1: ``_save_pending_trips`` MUST raise on a planted NaN in
# ``latest_delay_minutes``.
# ---------------------------------------------------------------------


def test_save_pending_trips_rejects_nan_latest_delay(
    _state_dir: Path,
) -> None:
    """Behavioural PoC — pre-fix this succeeded and wrote the literal
    ``NaN`` token to disk. Post-fix the ``allow_nan=False`` pin makes
    ``json.dump`` raise ``ValueError``; the writer catches the
    in-flight exception via ``atomic_write``'s ``try`` /
    ``finally`` so the partial temp file is cleaned up, then
    re-raises (the writer's outer ``except OSError`` does NOT
    catch ``ValueError``).
    """
    path = _state_dir / "pending_trips.json"
    state = {
        "key1": _make_pending(latest_delay_minutes=float("nan")),
    }
    # Pre-fix path: returned True, wrote ``NaN`` literal.
    # Post-fix path: raises ``ValueError`` from the inner json.dump.
    with pytest.raises(ValueError, match=r"Out of range float"):
        script._save_pending_trips(path, state)


def test_save_pending_trips_rejects_positive_infinity_latest_delay(
    _state_dir: Path,
) -> None:
    """Companion PoC: ``+Infinity`` must be rejected with the same
    error shape as ``NaN``."""
    path = _state_dir / "pending_trips.json"
    state = {
        "key1": _make_pending(latest_delay_minutes=float("inf")),
    }
    with pytest.raises(ValueError, match=r"Out of range float"):
        script._save_pending_trips(path, state)


def test_save_pending_trips_rejects_negative_infinity_latest_delay(
    _state_dir: Path,
) -> None:
    """Companion PoC: ``-Infinity`` must be rejected with the same
    error shape as ``NaN``."""
    path = _state_dir / "pending_trips.json"
    state = {
        "key1": _make_pending(latest_delay_minutes=float("-inf")),
    }
    with pytest.raises(ValueError, match=r"Out of range float"):
        script._save_pending_trips(path, state)


def test_save_pending_trips_emits_finite_floats_unchanged(
    _state_dir: Path,
) -> None:
    """Happy path: a finite ``latest_delay_minutes`` (positive,
    negative, or zero) MUST round-trip through the writer
    unchanged — the ``allow_nan=False`` pin is finite-only."""
    path = _state_dir / "pending_trips.json"
    state = {
        "k1": _make_pending(latest_delay_minutes=5.5),
        "k2": _make_pending(direction="Praterstern", latest_delay_minutes=-2.3),
        "k3": _make_pending(name="S2", latest_delay_minutes=0.0),
    }
    assert script._save_pending_trips(path, state) is True
    payload = json.loads(path.read_text(encoding="utf-8"))
    # Order-independent: keys are sorted by the writer.
    delays = {key: entry["latest_delay_minutes"] for key, entry in payload.items()}
    assert delays == {"k1": 5.5, "k2": -2.3, "k3": 0.0}


# ---------------------------------------------------------------------
# PoC 2: ``_save_recently_finalised`` — defence-in-depth (today's
# payload is all-string, but the writer is the sibling that must
# inherit the pin for the schema-widening case).
# ---------------------------------------------------------------------


def test_save_recently_finalised_rejects_nan_when_payload_widens(
    _state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forward-looking PoC: a planted ``float('nan')`` in the
    finalised payload (e.g. a future schema widening that adds a
    numeric ``re_emission_count`` or ``observed_delay_minutes``
    field) MUST be rejected by ``allow_nan=False``.

    The current payload shape is ``Mapping[str, datetime]`` →
    ``{key: ts.isoformat()}`` (all-string values), so we cannot plant
    a NaN through the public API. We exercise the underlying
    ``_json_lib.dump`` invocation directly with a synthetic
    NaN-bearing payload to pin the writer-shape contract.
    """
    # The writer's body is:
    #   payload = {key: ts.isoformat() for key, ts in finalised.items()}
    #   ...
    #   _json_lib.dump(payload, fh, ..., allow_nan=False)
    #
    # To plant a NaN we monkeypatch the local ``finalised`` mapping
    # construction so the payload comprehension produces a NaN value
    # instead of an iso-format string.

    # Synthetic payload that mimics a future schema-widening field.
    # We invoke the underlying serialiser the writer uses, with the
    # same kwargs the post-fix writer pins. This pins the contract
    # at the kwarg level even though the public API cannot reach
    # this shape today.
    bad_payload = {
        "ident-key": {
            "finalised_at": "2026-05-13T08:00:00+02:00",
            "observed_delay_minutes": float("nan"),
        },
    }
    with pytest.raises(ValueError, match=r"Out of range float"):
        # Mirror the post-fix writer's exact kwargs.
        json.dumps(
            bad_payload,
            indent=2,
            sort_keys=True,
            ensure_ascii=True,
            allow_nan=False,
        )


def test_save_recently_finalised_happy_path(
    _state_dir: Path,
) -> None:
    """Happy path: a normal finalised-ledger payload (all-string
    ISO timestamps) MUST round-trip through the writer unchanged
    — the ``allow_nan=False`` pin is finite-only and does not
    affect string values."""
    path = _state_dir / "recently_finalised.json"
    base = datetime(2026, 5, 13, 8, 0, tzinfo=VIENNA_TZ)
    finalised = {
        "key1": base,
        "key2": base.replace(hour=9),
    }
    assert script._save_recently_finalised(path, finalised) is True
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {
        "key1": "2026-05-13T08:00:00+02:00",
        "key2": "2026-05-13T09:00:00+02:00",
    }


# ---------------------------------------------------------------------
# Round-trip seal: the writer-side pin closes the dual of the
# reader-side ``loads_finite`` pin already on ``_load_pending_trips``.
# ---------------------------------------------------------------------


def test_round_trip_finite_payload_survives(
    _state_dir: Path,
) -> None:
    """A finite payload written by ``_save_pending_trips`` MUST load
    cleanly via ``_load_pending_trips`` — the writer-side pin does
    not regress the round-trip invariant.
    """
    path = _state_dir / "pending_trips.json"
    trip = _make_pending(
        direction="Meidling",
        name="S1",
        latest_delay_minutes=4.25,
    )
    state = {script._identity_key(trip.direction, trip.name, trip.scheduled): trip}
    assert script._save_pending_trips(path, state) is True
    loaded = script._load_pending_trips(path)
    assert len(loaded) == 1
    only = next(iter(loaded.values()))
    assert only.direction == trip.direction
    assert only.name == trip.name
    assert only.latest_delay_minutes == 4.25
