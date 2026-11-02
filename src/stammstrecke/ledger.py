"""Pending-trip and recently-finalised ledgers of the Stammstrecke monitors.

Load, save and purge helpers plus the advisory ledger lock. Moved
verbatim from ``scripts/update_stammstrecke_status.py``.
"""

from __future__ import annotations

import json as _json_lib
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from src.stammstrecke.core import (
    LOGGER,
    PENDING_TRIPS_MAX_BYTES,
    RECENTLY_FINALISED_MAX_BYTES,
    VIENNA_TZ,
    _PendingTrip,
)
from src.utils.files import atomic_write, loads_finite, read_capped_text
from src.utils.logging import sanitize_log_arg
from src.utils.vienna_clock import vienna_aware

# ---- Pending-trip state persistence ---------------------------------------


# Whitespace-runs and the pipe character are both removed from the
# raw VAO ``leg.name`` field before it is used as a state key or
# persisted to the ledger. Two motivations:
#
# 1. **Format drift.** Live ledger captures show VAO emitting
#    ``"S2"`` / ``"S3"`` (no space) while older / sibling deployments
#    use the spaced form ``"S 2"``. Without normalisation the same
#    physical train observed across a format flip would split into
#    two identity keys → both finalise → the train is counted twice
#    (the exact failure mode the dedup is supposed to prevent).
# 2. **Separator hardening.** :func:`_identity_key` joins the three
#    identity fields with ``|``. A poisoned upstream value containing
#    a literal pipe could collide with a different real key. Strip
#    the character at canonicalisation time so the separator is
#    invariant.
_NAME_NORMALISE_RE: Final = re.compile(r"\s+")


def _canonical_line_name(value: object) -> str:
    """Strip whitespace runs + pipe characters, upper-case the rest.

    ``"S 2"`` / ``"S2"`` / ``" s2 "`` → ``"S2"``. Empty / whitespace-
    only / ``None`` input returns the empty string; callers reject
    such legs upstream so the value never enters the ledger.

    *value* is typed as :class:`object` so the function can be the
    single canonicalisation point regardless of whether the caller
    has a guaranteed-``str`` value (``leg.get("name") or ""``) or a
    heterogeneous ``Mapping[str, Any]`` slot (``data.get("name")``)
    — the body coerces via ``str(value) if value is not None else ""``
    to avoid the falsy-collapse of ``str(0 or "")`` returning ``""``.
    """

    cleaned = _NAME_NORMALISE_RE.sub(
        "", str(value) if value is not None else ""
    ).replace("|", "")
    return cleaned.upper()


def _identity_key(direction: str, name: str, scheduled: datetime) -> str:
    """Build the canonical state-key for one observed S-Bahn trip.

    Composed of the three fields that uniquely identify a physical
    train run in our use case: the monitored direction
    (``Meidling``/``Floridsdorf``), the canonicalised line
    designation (see :func:`_canonical_line_name`) and the
    *scheduled* origin departure timestamp.
    Two different trains of the same line cannot share a scheduled
    departure from the same station at the same second, so the tuple
    is collision-free for our scope without requiring a VAO-internal
    journey reference.

    The pipe (``|``) separator is chosen because neither the
    direction label nor the canonicalised line name contain it (the
    canonicaliser strips it). ``direction`` is one of two hardcoded
    labels so no normalisation is required there.
    """

    return f"{direction}|{_canonical_line_name(name)}|{scheduled.isoformat()}"


def _coerce_aware(value: datetime) -> datetime:
    """*value* in :data:`VIENNA_TZ`; a naive wall clock is read as Vienna time.

    The ledgers are written with Vienna offsets, so an aware value with
    another offset (a hand-edited entry) is converted when it is read.
    :func:`src.utils.vienna_clock.vienna_aware` localises the naive case.
    """
    return vienna_aware(value).astimezone(VIENNA_TZ)


def _trip_to_json(trip: _PendingTrip) -> dict[str, Any]:
    """Serialise a pending trip to its JSON-ledger form.

    The ``cancelled`` field is always emitted (even when ``False``) so a
    grep over the committed ledger picks up the schema version
    explicitly — reviewers do not have to infer the boolean from its
    absence. Backwards-compat is enforced on the read side
    (:func:`_trip_from_json` defaults missing values to ``False``).
    """

    return {
        "direction": trip.direction,
        "name": trip.name,
        "scheduled": trip.scheduled.isoformat(),
        "latest_delay_minutes": trip.latest_delay_minutes,
        "last_seen_at": trip.last_seen_at.isoformat(),
        "cancelled": trip.cancelled,
    }


def _trip_from_json(data: Mapping[str, Any]) -> _PendingTrip | None:
    """Best-effort parser for a single ledger entry; ``None`` on shape error.

    The ``name`` field flows through :func:`_canonical_line_name` so an
    old ledger written before the H1 normalisation fix (which carried
    spaced names like ``"S 2"``) is migrated to the canonical form on
    next load — without that step, an entry stored as ``"S 2"`` would
    fail to match a freshly-observed ``"S2"`` on the next tick and the
    train would be tracked twice.
    """

    try:
        direction = str(data["direction"]).strip()
        name = _canonical_line_name(data.get("name"))
        scheduled = datetime.fromisoformat(str(data["scheduled"]))
        latest_delay = float(data["latest_delay_minutes"])
        last_seen = datetime.fromisoformat(str(data["last_seen_at"]))
    except (KeyError, TypeError, ValueError):
        return None
    if not direction or not name:
        return None
    # Backwards-compat: a ledger entry written before the cancellation-
    # tracking schema (commits prior to 2026-05-15) has no ``cancelled``
    # key — default to ``False`` so the entry loads as a regular
    # delay-bearing observation. Strict ``is True`` matches the upstream
    # VAO contract for the boolean and refuses ``"true"`` / ``1`` from a
    # hand-edited ledger (the writer always emits a Python bool).
    cancelled = data.get("cancelled") is True
    return _PendingTrip(
        direction=direction,
        name=name,
        scheduled=_coerce_aware(scheduled),
        latest_delay_minutes=latest_delay,
        last_seen_at=_coerce_aware(last_seen),
        cancelled=cancelled,
    )


def _load_pending_trips(path: Path) -> dict[str, _PendingTrip]:
    """Read the pending-trip ledger from *path*; corruption-tolerant.

    Returns an empty dict on missing / oversize / unparseable input;
    each diagnostic is logged at WARNING so an operator can spot the
    fresh-start fallback without the script ever blocking the cron
    pipeline.
    """

    raw = read_capped_text(
        path,
        max_bytes=PENDING_TRIPS_MAX_BYTES,
        label="pending trips",
        logger=LOGGER,
    )
    if raw is None:
        return {}
    if not raw.strip():
        return {}
    try:
        # Security: ``loads_finite`` pins parse_constant + parse_float
        # hooks (Round 1503 sibling) that reject NaN / Infinity / 1e1000
        # literals planted into a poisoned ``cache/stammstrecke/
        # pending_trips.json`` (compromised CI runner / partial flush +
        # power loss / parallel orchestrator atomic state swap). Without
        # the hooks the planted literal propagates as ``float('nan')`` /
        # ``float('inf')`` past the downstream ``_trip_from_json``
        # validators (which use ``isinstance(value, (int, float))``
        # checks that ``True`` on ``float('nan')``) and round-trip-
        # crashes the writer pin (Round 1485) on next save.
        payload = loads_finite(raw)
    except (ValueError, RecursionError) as exc:
        LOGGER.warning(
            "Pending-Trips-State korrupt (%s) — starte mit leerem Ledger.",
            sanitize_log_arg(str(exc)),
        )
        return {}
    if not isinstance(payload, Mapping):
        LOGGER.warning(
            "Pending-Trips-State hat unerwartetes Top-Level-Format — "
            "starte mit leerem Ledger."
        )
        return {}
    state: dict[str, _PendingTrip] = {}
    for value in payload.values():
        if not isinstance(value, Mapping):
            continue
        trip = _trip_from_json(value)
        if trip is None:
            continue
        canonical_key = _identity_key(trip.direction, trip.name, trip.scheduled)
        state[canonical_key] = trip
    return state


def _save_pending_trips(path: Path, state: Mapping[str, _PendingTrip]) -> bool:
    """Persist *state* atomically; best-effort.

    Returns ``True`` on success, ``False`` if the write failed (and
    was logged at WARNING). The caller is free to ignore the return
    value — losing one tick's state update means the affected trips
    keep their previous state on next load, which is the desired
    safe-fallback shape.

    Security (Trojan-Source / BiDi-Mark Drift, sibling of PRs #1434 /
    #1435 and Rounds 10–14): the file is operator-facing diagnostic
    state, committed to ``main`` by ``update-cycle.yml``'s
    ``add_options: '-A'`` auto-commit step, and reviewed via ``cat``
    / ``less`` / the GitHub web UI / IDE preview. ``ensure_ascii=True``
    escapes every non-ASCII code point as a literal ``\\uXXXX``
    sequence so a VAO-upstream-controlled ``leg.name`` carrying the
    canonical CVE-2021-42574 BiDi / zero-width / Unicode line-
    terminator / 8-bit C1 union cannot leak as raw UTF-8 bytes:
    ``_canonical_line_name`` only strips ``\\s+`` (which excludes
    U+202E and the rest of category Cf) plus ``|``, so the
    upstream-derived field reaches the dict KEY (via
    ``_identity_key``) and the inner ``name`` value verbatim.
    Mirrors the canonical fix shape pinned for the sibling
    ``data/*.json`` / ``cache/<provider>/last_run.json`` state
    writers; forensic intent is preserved
    (``_load_pending_trips`` recovers the original string from the
    literal escape via ``json.loads``). Legitimate payload content
    is exclusively ASCII (hardcoded direction labels, short S-Bahn
    / R / REX / CJX line designations, ISO-8601 timestamps), so the
    diff shape is unchanged on the happy path.

    Security (Non-Finite Literal Writer-Defence Drift, sibling of
    PRs #1487 / #1488 — coordinate + companion-writer rounds):
    ``allow_nan=False`` mirrors the canonical writer-side pin
    established for the sibling state-sink writers
    (``data/first_seen.json`` / ``data/stations_last_run.json`` /
    ``data/vor_request_count.json`` /
    ``cache/<provider>/last_run.json``). ``_trip_to_json`` emits
    ``"latest_delay_minutes": trip.latest_delay_minutes`` directly
    from the concrete ``float`` field; a future refactor of
    :func:`_leg_departure_delay_minutes` that lets ``float('nan')``
    / ``float('inf')`` reach ``latest_delay_minutes`` (missing-data
    sentinel, third-party SDK NaN observation, derived-statistic
    division) would otherwise plant the non-standard literal —
    invalid per RFC 8259 §6 — in the committed artefact. This pin
    is also the writer-side dual of the reader-side ``loads_finite``
    hook on :func:`_load_pending_trips`: together they enforce the
    round-trip invariant that no non-finite literal can enter or
    leave the on-disk state without surfacing as a loud
    ``ValueError`` at the producing call.
    """

    payload = {key: _trip_to_json(trip) for key, trip in state.items()}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with atomic_write(
            path,
            mode="w",
            encoding="utf-8",
            permissions=0o644,
        ) as fh:
            _json_lib.dump(
                payload,
                fh,
                indent=2,
                sort_keys=True,
                ensure_ascii=True,
                allow_nan=False,
            )
            fh.write("\n")
        return True
    except OSError as exc:
        LOGGER.warning(
            "Pending-Trips-State konnte nicht geschrieben werden: %s",
            sanitize_log_arg(str(exc)),
        )
        return False


def _purge_stale_entries(
    state: dict[str, _PendingTrip],
    *,
    cutoff: datetime,
) -> int:
    """Drop entries whose ``last_seen_at`` is strictly before *cutoff*.

    Returns the number of entries removed. Mutates *state* in place.
    """

    stale = [key for key, trip in state.items() if trip.last_seen_at < cutoff]
    for key in stale:
        del state[key]
    return len(stale)


def _load_recently_finalised(path: Path) -> dict[str, datetime]:
    """Read the recently-finalised companion ledger from *path*.

    Schema: ``{identity_key: iso-8601-timestamp}``. Returns an empty
    dict on missing / oversize / unparseable input — the WARNING log
    line distinguishes the silent fresh-start from a healthy first
    run.
    """

    raw = read_capped_text(
        path,
        max_bytes=RECENTLY_FINALISED_MAX_BYTES,
        label="recently finalised",
        logger=LOGGER,
    )
    if raw is None:
        return {}
    if not raw.strip():
        return {}
    try:
        # Security: same parse_constant + parse_float hook pin as the
        # pending-trips ledger above — both sidecars share the same
        # disk-tainted threat model (Round 1503 sibling). A poisoned
        # ``cache/stammstrecke/recently_finalised.json`` would otherwise
        # land ``float('nan')`` in the timestamp field, breaking the
        # subsequent ``datetime.fromisoformat`` shape check at the
        # ``str`` instance test below but still leaking through the
        # writer-pin round-trip the next time the ledger is rewritten.
        payload = loads_finite(raw)
    except (ValueError, RecursionError) as exc:
        LOGGER.warning(
            "Recently-Finalised-Ledger korrupt (%s) — starte mit leerem Set.",
            sanitize_log_arg(str(exc)),
        )
        return {}
    if not isinstance(payload, Mapping):
        LOGGER.warning(
            "Recently-Finalised-Ledger hat unerwartetes Top-Level-Format — "
            "starte mit leerem Set."
        )
        return {}
    out: dict[str, datetime] = {}
    for key, value in payload.items():
        if not isinstance(value, str):
            continue
        try:
            ts = datetime.fromisoformat(value)
        except ValueError:
            continue
        out[str(key)] = _coerce_aware(ts)
    return out


def _save_recently_finalised(
    path: Path, finalised: Mapping[str, datetime]
) -> bool:
    """Persist the recently-finalised companion ledger atomically.

    Security: same Trojan-Source / BiDi-Mark threat model as
    :func:`_save_pending_trips` — the keys are built via
    :func:`_identity_key` which interpolates a VAO-upstream-controlled
    leg-name segment, so a hostile / pathological upstream payload
    surfaces here through the same path. ``ensure_ascii=True`` escapes
    every non-ASCII code point so no raw byte in the canonical attack
    union reaches the committed
    ``cache/stammstrecke/recently_finalised.json``.

    Security (Non-Finite Literal Writer-Defence Drift, defence-in-
    depth sibling pin of :func:`_save_pending_trips`):
    ``allow_nan=False`` is set in lockstep with the canonical writer
    pin even though today's payload is all-string
    (``{key: ts.isoformat()}`` — ``isoformat()`` always returns a
    finite-byte string). The pin protects against a future schema
    widening that adds a numeric field (re-emission count, age-in-
    seconds for cleanup tooling, observed-delay arithmetic) — the
    sibling pin keeps the writer-shape contract uniform across the
    two-file ledger pair so a future refactor cannot regress one
    half of the round-trip invariant.
    """

    payload = {key: ts.isoformat() for key, ts in finalised.items()}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with atomic_write(
            path,
            mode="w",
            encoding="utf-8",
            permissions=0o644,
        ) as fh:
            _json_lib.dump(
                payload,
                fh,
                indent=2,
                sort_keys=True,
                ensure_ascii=True,
                allow_nan=False,
            )
            fh.write("\n")
        return True
    except OSError as exc:
        LOGGER.warning(
            "Recently-Finalised-Ledger konnte nicht geschrieben werden: %s",
            sanitize_log_arg(str(exc)),
        )
        return False


def _purge_finalised_entries(
    finalised: dict[str, datetime],
    *,
    cutoff: datetime,
) -> int:
    """Drop finalisation records older than *cutoff*; in-place.

    Once the TTL has elapsed, re-emission of a long-finalised train is
    indistinguishable from a fresh observation — falling out of the
    suppression set is the conservative choice (versus retaining
    forever, which would also count as state-file rot).
    """

    stale = [key for key, ts in finalised.items() if ts < cutoff]
    for key in stale:
        del finalised[key]
    return len(stale)


@contextmanager
def _ledger_lock(lock_path: Path) -> Iterator[None]:
    """OS-level advisory lock around the load-modify-save ledger sequence.

    Uses :mod:`fcntl` on POSIX runners (the production target). On
    platforms without ``fcntl`` (Windows dev boxes) the lock degrades
    to a no-op — same risk model as the pre-lock code, and the
    project's production runners are all Linux.

    The lock file is opened in append mode so the first caller
    creates it without truncating any existing content (we only use
    its file descriptor; the file body is irrelevant). A WARNING is
    logged on lock-acquire failures so an operator sees the
    degradation explicitly.
    """

    try:
        import fcntl  # POSIX-only; fcntl is in the stdlib on Linux.
    except ImportError:  # pragma: no cover - non-POSIX dev machines.
        yield
        return

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = lock_path.open("a", encoding="utf-8")
    except OSError as exc:
        LOGGER.warning(
            "Konnte Ledger-Lock-Datei nicht öffnen (%s) — fahre ohne Lock fort.",
            sanitize_log_arg(str(exc)),
        )
        yield
        return
    try:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
        except OSError as exc:
            LOGGER.warning(
                "Ledger-Lock konnte nicht erworben werden (%s) — fahre ohne "
                "Lock fort.",
                sanitize_log_arg(str(exc)),
            )
            yield
            return
        try:
            yield
        finally:
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            except OSError as exc:
                LOGGER.debug(
                    "Ledger-Lock konnte nicht freigegeben werden: %s",
                    sanitize_log_arg(str(exc)),
                )
    finally:
        try:
            fd.close()
        except OSError as exc:
            LOGGER.debug(
                "Ledger-Lock-Datei konnte nicht geschlossen werden: %s",
                sanitize_log_arg(str(exc)),
            )
