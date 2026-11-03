"""The VAO daily-budget ledger of the VOR provider.

``data/vor_request_count.json``, the in-process cache in front of it, the
exclusive-file-lock reservation and the ``atexit`` flush. Split off
``src/providers/vor.py`` (700-line rule, 2026-11-02); that module re-exports
the public functions and stays the public entry point.

All quota state lives here and nowhere else: ``REQUEST_COUNT_FILE``,
``MAX_VOR_QUOTA_FILE_BYTES``, ``_QUOTA_CACHE``, ``_QUOTA_LOCK`` and the
``file_lock`` binding the ledger functions use. A test that redirects the
ledger patches this module (``src.providers.vor_quota``), not the facade.

``MAX_REQUESTS_PER_DAY``, ``log`` and ``_resolve_path`` come from
``src.providers.vor``, imported at the end of this module: the facade imports
this module only after it has defined them, and this module defines every name
the facade takes from it before it imports the facade, so either import order
works.
"""

from __future__ import annotations

import atexit
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from collections.abc import Mapping

from zoneinfo import ZoneInfo

from ..utils.files import atomic_write, read_capped_json
from ..utils.locking import file_lock
from ..utils.logging import sanitize_log_arg
from .vor_env import DATA_DIR, _get_env

# Global lock for thread-safe quota management within the process
_QUOTA_LOCK = threading.RLock()
# Local cache to optimize quota checks (fail-fast)
_QUOTA_CACHE: dict[str, Any] = {"date": None, "count": 0, "unsaved_delta": 0}


def _flush_quota_cache() -> None:
    """Flush any unsaved request counts to disk on exit.

    Persists the in-memory ``unsaved_delta`` to disk WITHOUT recording a
    new request. Pre-fix this function called :func:`save_request_count`,
    which always increments ``unsaved_delta`` by ``1`` before persisting
    — every script invocation that made any VOR call therefore booked
    one phantom request beyond the actual API traffic. With 48 cron
    ticks/day and 2 real ``/trip`` calls per tick, the bug inflated the
    persisted counter from ``96`` to ``144`` requests/day, exhausting
    the contractual ``100``/day VAO Start cap after roughly 33 ticks
    and leaving the remaining ~15 hours of the day without
    Stammstrecke observations. The CSV ledger gap that bug produced
    visibly degraded the README "Letzte 60 Minuten" snapshot when one
    of the affected hours was the most recent one.
    """
    with _QUOTA_LOCK:
        _persist_quota_to_disk()

atexit.register(_flush_quota_cache)


def _write_request_count_file(path: Path, payload: Mapping[str, Any]) -> None:
    """Atomically write the VOR per-day request-count payload to *path*.

    Security (Trojan-Source / BiDi-Mark Drift Round 11): the file is
    operator-facing diagnostic state, committed to ``main`` by the
    IFTTT-triggered ``update-cycle.yml`` (Stammstrecke step) and by
    the ``update-vor-cache.yml`` operator-only escape hatch (both
    list ``data/vor_request_count.json`` in their ``file_pattern``
    or via ``add_options: -A``) and reviewed via ``cat`` / ``less``
    / the GitHub web UI / IDE preview. ``ensure_ascii=True`` escapes every non-ASCII
    code point as a literal ``\\uXXXX`` sequence, so a future request-
    count payload field carrying station- / provider- / environment-
    controlled content cannot leak the canonical CVE-2021-42574
    Trojan-Source / zero-width / Unicode-line-terminator / 8-bit C1
    union as raw UTF-8 bytes. Mirrors the canonical fix shape pinned
    in PR #1434 / PR #1435 for the sibling ``data/*.json`` sidecar
    writers (``_write_quarantine_file``, ``_save_state``,
    ``_write_heartbeat_file``). Forensic intent is preserved
    (``load_request_count`` recovers the original string from the
    literal escape via ``json.loads``).

    Security (Coordinate finite/range drift, committed-writer
    defence-in-depth): ``allow_nan=False`` mirrors the canonical
    writer-side pin established in Round 1485 at
    :func:`src.places.merge.write_stations` and extended in Round
    1487 to the sibling stations / cache-events writers. The
    payload is a ``Mapping[str, Any]`` so any future field
    (e.g. a fractional response-rate metric, latency average)
    inherits the missing pin and could land non-standard
    ``NaN`` / ``Infinity`` literals (invalid per RFC 8259) in
    the committed ``data/vor_request_count.json`` sidecar.
    """
    with atomic_write(path, mode="w", encoding="utf-8", permissions=0o600) as handle:
        json.dump(payload, handle, ensure_ascii=True, allow_nan=False)
        handle.write("\n")



# Security: byte cap for the on-disk quota parser in this module. Pre-fix the
# site used the unsafe ``Path.read_text()`` -> ``json.loads()`` shape with no
# size cap whatsoever. A planted huge file (compromised CI runner / partial
# flush + power loss / parallel orchestrator's atomic state swap) buffered the
# entire file into memory and propagated ``MemoryError`` (a ``BaseException``
# subclass that is NOT caught by ``except (FileNotFoundError, OSError,
# json.JSONDecodeError, RecursionError)``) past every catch tuple — crashing the
# per-request quota debit (``load_request_count`` / ``save_request_count``).
# ``vor_request_count.json`` is a single small object
# ``{"date": "...", "requests": N}`` (~50 bytes); 1 MiB is far above that.
# Mirrors the per-loader cap pattern in ``src/utils/cache.py``
# (``MAX_CACHE_FILE_BYTES``).
MAX_VOR_QUOTA_FILE_BYTES = 1 * 1024 * 1024


def load_request_count(bypass_cache: bool = False) -> tuple[str | None, int]:
    # Check memory cache first
    vienna_tz = ZoneInfo("Europe/Vienna")
    today_local = datetime.now(vienna_tz).strftime("%Y-%m-%d")

    if not bypass_cache and _QUOTA_CACHE["date"] == today_local:
        # Return the full in-memory usage. ``count`` only holds the last
        # *flushed* total; live reservations accumulate in ``unsaved_delta``
        # between batch flushes. Every other read site (the disk path below
        # and ``save_request_count``) sums both, so the cache-hit path must
        # too — otherwise it under-reports usage by up to one flush batch and
        # defeats the ``_charge_one_request`` pre-flight quota check.
        return (today_local, cast(int, _QUOTA_CACHE["count"] + _QUOTA_CACHE.get("unsaved_delta", 0)))

    # Security: ``read_capped_json`` enforces a TOCTOU-safe 1 MiB cap and
    # returns ``None`` for missing / oversized / depth-bombed / corrupt
    # files — closes the ``Path.read_text()`` -> ``MemoryError``
    # propagation that would otherwise escape past the prior catch tuple
    # ``(FileNotFoundError, OSError, json.JSONDecodeError, RecursionError)``
    # (``MemoryError`` is ``BaseException``-rooted and bypasses every
    # subclass-of-``Exception`` catch). ``load_request_count`` is invoked
    # by every VOR fetch in the pipeline; an unbounded read at this site
    # would crash the entire daily quota debit chain.
    data = read_capped_json(
        REQUEST_COUNT_FILE,
        MAX_VOR_QUOTA_FILE_BYTES,
        label="VOR quota",
        logger=log,
    )
    if not isinstance(data, dict):
        return (None, 0)

    stored_date = data.get("date")
    # Using 'requests' key as per new strict schema requirement.
    if stored_date == today_local and "requests" in data:
        count = data["requests"]
        try:
            int_count = int(count)
        except (ValueError, TypeError):
            int_count = 0
        # Security: a poisoned ``data/vor_request_count.json`` (planted by
        # a compromised CI runner, partial flush + power loss, or operator
        # mis-edit) could record a NEGATIVE ``requests`` value. The
        # downstream ``_limit_reached`` check (``scripts/update_vor_cache.py:87``)
        # uses ``todays_count >= MAX_REQUESTS_PER_DAY``, which negative
        # values silently bypass — and ``save_request_count`` perpetuates
        # the negative offset by adding the run's delta to it and writing
        # back. The net effect is that legitimate runs continue to fetch
        # under the projected-usage cap, but the runtime quota counter
        # stays artificially low for many days, breaching defense-in-depth
        # for the contractually-strict VAO Start tier 100/day limit.
        # Clamp at 0 so a tampered file cannot drive the in-memory or
        # on-disk counter below zero; the next save_request_count() flush
        # rewrites the canonical [0, MAX] schema and self-heals.
        int_count = max(0, int_count)

        # Update cache
        _QUOTA_CACHE["date"] = stored_date
        _QUOTA_CACHE["count"] = int_count

        return (stored_date, int_count + _QUOTA_CACHE.get("unsaved_delta", 0))

    # Discard legacy formats (raw integers, 'count' key) or old dates
    return (None, 0)


def _persist_quota_to_disk(force_date: str | None = None) -> int:
    """Persist any pending ``unsaved_delta`` to disk; **does not increment**.

    Caller MUST hold :data:`_QUOTA_LOCK`. Returns the new on-disk total
    (or the unchanged in-memory total when there was nothing to flush).

    *force_date* overrides the day the delta is booked under. It is used
    by :func:`save_request_count` to flush a still-pending prior-day delta
    under the PRIOR day's date when a long-lived process crosses the Vienna
    midnight boundary — otherwise the default (current Vienna day) would
    mis-credit those requests to the new day and zero the prior ledger.

    Split off from :func:`save_request_count` 2026-05-15 to fix a
    quota-inflation bug: the previous :func:`_flush_quota_cache` invoked
    :func:`save_request_count`, which incremented ``unsaved_delta`` by
    one before flushing — every script invocation that made any VOR
    call therefore booked one phantom request beyond the actual API
    traffic. The split makes the persist path callable without that
    side effect while keeping :func:`save_request_count` semantically
    identical for every existing caller (increment-then-conditionally-
    flush). All security invariants (TOCTOU-safe capped read, negative-
    count clamp, atomic write, lock-failure quota-poison sentinel) live
    in this helper now and are exercised through both entry points.

    Cross-process safety: the persisted total is clamped at
    :data:`MAX_REQUESTS_PER_DAY` under the file lock. The ``< MAX``
    fail-fast in :func:`save_request_count` / ``_charge_one_request``
    only consults the per-process cache, so two concurrent processes
    (e.g. overlapping ``workflow_dispatch`` runs not covered by the
    ``external-api-fetch`` concurrency group) could each pass the check at
    ``count == MAX-1`` and both increment, pushing the persisted total
    past the hard daily budget (-> 101/100). The clamp makes that
    impossible; it is a no-op for the single-process fail-fast path.
    """
    if _QUOTA_CACHE.get("unsaved_delta", 0) <= 0:
        return cast(int, _QUOTA_CACHE.get("count", 0))

    if force_date is not None:
        date_iso = force_date
    else:
        vienna_tz = ZoneInfo("Europe/Vienna")
        now_local = datetime.now(vienna_tz)
        date_iso = now_local.strftime("%Y-%m-%d")

    # Ensure the parent directory exists before attempting to open the lock file.
    # This prevents FileNotFoundError if the directory structure is missing.
    REQUEST_COUNT_FILE.parent.mkdir(parents=True, exist_ok=True)

    lock_path = REQUEST_COUNT_FILE.with_suffix(".lock")

    try:
        with (
            lock_path.open("a+", encoding="utf-8") as lock_file,
            file_lock(lock_file, exclusive=True),
        ):
            # Security: TOCTOU-safe size cap via read_capped_json.
            disk_date = None
            disk_count = 0
            data = read_capped_json(
                REQUEST_COUNT_FILE,
                MAX_VOR_QUOTA_FILE_BYTES,
                label="VOR quota",
                logger=log,
            )
            if isinstance(data, dict):
                disk_date = data.get("date")
                if "requests" in data:
                    try:
                        disk_count = int(data["requests"])
                    except (ValueError, TypeError):
                        # Unparseable ``requests`` field (string,
                        # ``null``, malformed) → fall through to the
                        # default ``disk_count = 0``. The next write
                        # rewrites the file with the canonical schema
                        # so the corruption self-heals on the next
                        # cron tick. Mirrors the silent-bail shape of
                        # ``load_request_count`` for the same field.
                        pass

            # Security: clamp at 0 to defeat negative-count quota-bypass
            # (mirrors load_request_count's clamp; see its comment block).
            disk_count = max(0, disk_count)

            if disk_date != date_iso:
                disk_count = 0
                _QUOTA_CACHE["count"] = 0
            else:
                # Update our memory cache to reflect exactly what's on disk right now
                _QUOTA_CACHE["count"] = disk_count

            # Add our unsaved delta, clamped at the hard daily budget (cross-process race; see docstring).
            new_total = min(disk_count + _QUOTA_CACHE["unsaved_delta"], MAX_REQUESTS_PER_DAY)

            _QUOTA_CACHE["count"] = new_total
            _QUOTA_CACHE["date"] = date_iso
            _QUOTA_CACHE["unsaved_delta"] = 0

            payload = {"date": date_iso, "requests": new_total}
            try:
                # Centralised atomic + ASCII-safe write —
                # see ``_write_request_count_file`` for the
                # Trojan-Source threat model.
                _write_request_count_file(REQUEST_COUNT_FILE, payload)
            except OSError:
                log.critical("Failed to write to request count file. Quota mechanism poisoned.")
                _QUOTA_CACHE["count"] = MAX_REQUESTS_PER_DAY + 1
                _QUOTA_CACHE["unsaved_delta"] = 0
                return MAX_REQUESTS_PER_DAY + 1
    except (OSError, TimeoutError) as e:
        # Sentinel: file/lock errors can carry attacker-controlled
        # path fragments from a planted lockfile name; sanitise
        # before logging.
        log.warning(
            "Failed to save request count (lock error): %s",
            sanitize_log_arg(str(e)),
        )
        # Audit A.3: poison the in-memory cache on the LOCK-failure path too.
        # Pre-fix only the inner ``OSError`` (write/replace failure) branch
        # above poisoned the cache; this outer branch merely *returned* the
        # ``MAX + 1`` sentinel. Because the sole caller
        # (``_charge_one_request``) gates on ``load_request_count()`` — which
        # reads the cache — an unreadable lockfile left the gate wide open:
        # every subsequent call re-read an unchanged, low cache value, passed
        # the fail-fast and fired a real VAO request, while nothing was ever
        # persisted. Poisoning here makes the sentinel effective in-process
        # and fails closed, symmetric with the write-failure branch.
        _QUOTA_CACHE["count"] = MAX_REQUESTS_PER_DAY + 1
        _QUOTA_CACHE["unsaved_delta"] = 0
        _QUOTA_CACHE["date"] = date_iso
        return MAX_REQUESTS_PER_DAY + 1

    return cast(int, new_total)


def _read_quota_file_unlocked() -> tuple[str | None, int]:
    """Return ``(stored_date, requests)`` from the on-disk quota ledger.

    Shared by the locked reservation path below. Uses the same TOCTOU-safe,
    size-capped loader as :func:`load_request_count` and applies the same
    negative-count clamp, so a poisoned ledger cannot drive the counter below
    zero and bypass the cap. Returns ``(None, 0)`` for a missing / corrupt /
    oversized file; the next successful write rewrites the canonical schema.
    """
    data = read_capped_json(
        REQUEST_COUNT_FILE,
        MAX_VOR_QUOTA_FILE_BYTES,
        label="VOR quota",
        logger=log,
    )
    if not isinstance(data, dict):
        return (None, 0)
    stored_date = data.get("date")
    date_str = stored_date if isinstance(stored_date, str) else None
    raw_count = data.get("requests")
    if isinstance(raw_count, bool):
        # ``int(True) == 1`` would silently credit a boolean ledger value as a
        # real reservation. Mirrors ``preflight_quota_check._coerce_int``.
        return (date_str, 0)
    try:
        count = int(raw_count)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        count = 0
    return (date_str, max(0, count))


def _reserve_request_slot_locked(date_iso: str) -> tuple[bool, int]:
    """Reserve one VAO request slot atomically. Caller MUST hold :data:`_QUOTA_LOCK`.

    Returns ``(granted, total)`` — ``granted`` is ``False`` when the daily
    budget is already spent or the ledger could not be updated, in which case
    ``total`` carries the observed count (or the ``MAX + 1`` poison sentinel).

    Audit A.2 — why this replaces the previous check-then-flush split:
    ``_QUOTA_LOCK`` is a :class:`threading.RLock` and therefore scopes to ONE
    process. The old shape checked the budget against the per-process cache,
    incremented in memory, and only took the *file* lock later, at flush time,
    where an over-budget total was silently clamped to
    :data:`MAX_REQUESTS_PER_DAY`. That clamp bounded the number written to the
    ledger — it never bounded the number of requests actually put on the wire.
    Reproduced before this fix with six concurrent processes: 104 real
    reservations were granted while the ledger reported exactly 100, hiding
    four calls beyond the contractual VAO Start cap.

    Read, budget check, increment and persist now all happen inside a single
    ``file_lock(..., exclusive=True)`` critical section, so two processes
    racing at ``count == MAX - 1`` serialise: the first is granted the last
    slot, the second observes ``MAX`` on disk and is refused *before* it can
    issue a request. The clamp is gone because it can no longer be reached.
    """
    REQUEST_COUNT_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_path = REQUEST_COUNT_FILE.with_suffix(".lock")

    try:
        with (
            lock_path.open("a+", encoding="utf-8") as lock_file,
            file_lock(lock_file, exclusive=True),
        ):
            disk_date, disk_count = _read_quota_file_unlocked()
            if disk_date != date_iso:
                disk_count = 0

            # Fold a still-pending in-memory delta booked for the SAME day so
            # a legacy batched reservation cannot be lost when a caller mixes
            # ``save_request_count`` with the atomic path.
            pending = 0
            if _QUOTA_CACHE.get("date") == date_iso:
                pending = max(0, int(_QUOTA_CACHE.get("unsaved_delta", 0) or 0))
            base = min(disk_count + pending, MAX_REQUESTS_PER_DAY)

            if base >= MAX_REQUESTS_PER_DAY:
                _QUOTA_CACHE["date"] = date_iso
                _QUOTA_CACHE["count"] = base
                _QUOTA_CACHE["unsaved_delta"] = 0
                return (False, base)

            new_total = base + 1
            try:
                # Centralised atomic + ASCII-safe write — see
                # ``_write_request_count_file`` for the Trojan-Source threat
                # model. The reservation is only granted once this returns:
                # a request is never put on the wire against a slot that
                # could not be persisted.
                _write_request_count_file(
                    REQUEST_COUNT_FILE, {"date": date_iso, "requests": new_total}
                )
            except OSError:
                log.critical(
                    "Failed to write to request count file. Quota mechanism poisoned."
                )
                _QUOTA_CACHE["count"] = MAX_REQUESTS_PER_DAY + 1
                _QUOTA_CACHE["unsaved_delta"] = 0
                _QUOTA_CACHE["date"] = date_iso
                return (False, MAX_REQUESTS_PER_DAY + 1)

            _QUOTA_CACHE["date"] = date_iso
            _QUOTA_CACHE["count"] = new_total
            _QUOTA_CACHE["unsaved_delta"] = 0
            return (True, new_total)
    except (OSError, TimeoutError) as exc:
        # Sentinel: lock errors can carry attacker-controlled path fragments
        # from a planted lockfile name; sanitise before logging.
        log.warning(
            "Failed to reserve request slot (lock error): %s",
            sanitize_log_arg(str(exc)),
        )
        # Audit A.3: fail closed. Without a usable lock the budget cannot be
        # accounted for, so refuse the reservation AND poison the cache so a
        # cache-reading gate cannot wave through the next call either.
        _QUOTA_CACHE["count"] = MAX_REQUESTS_PER_DAY + 1
        _QUOTA_CACHE["unsaved_delta"] = 0
        _QUOTA_CACHE["date"] = date_iso
        return (False, MAX_REQUESTS_PER_DAY + 1)


def reserve_request_slot(now: datetime | None = None) -> tuple[bool, int]:
    """Atomically reserve one VAO request slot.

    This is the primitive every VOR consumer should use before issuing a
    request: it performs the budget check and the increment in a single
    exclusive-file-lock critical section, so the decision cannot be raced by a
    concurrent process (see :func:`_reserve_request_slot_locked`).

    Args:
        now: Optional reference instant. Only its Europe/Vienna calendar date
            is used, to pick the ledger day. Defaults to the current time.

    Returns:
        ``(granted, total)``. When ``granted`` is ``True`` the caller owns one
        reservation and may issue exactly one request. When it is ``False`` the
        caller MUST NOT issue a request; ``total`` is the observed daily count,
        or ``MAX_REQUESTS_PER_DAY + 1`` when the ledger could not be updated.
    """
    vienna_tz = ZoneInfo("Europe/Vienna")
    reference = now.astimezone(vienna_tz) if now is not None else datetime.now(vienna_tz)
    date_iso = reference.strftime("%Y-%m-%d")
    with _QUOTA_LOCK:
        return _reserve_request_slot_locked(date_iso)


def save_request_count(now_ignored: datetime | None = None) -> int:
    """Reserve one request slot and return the resulting daily total.

    Thin backwards-compatible wrapper around :func:`reserve_request_slot`.
    The historical contract is preserved exactly: the return value is the
    daily total after the call, an exhausted budget returns the unchanged
    total without incrementing, and a lock/write failure returns the
    ``MAX_REQUESTS_PER_DAY + 1`` poison sentinel.

    What changed (audit A.2) is the *timing*: reservations are no longer
    buffered in memory and flushed in batches — every call now persists
    inside the exclusive file lock. The batched shape traded a cross-process
    budget guarantee for file I/O that the production cadence never needed
    (the Stammstrecke monitor makes a single request per cron tick, so the
    old code flushed on that very first call anyway).
    """
    _granted, total = reserve_request_slot()
    return total


from .vor import (  # noqa: E402
    MAX_REQUESTS_PER_DAY,
    _resolve_path,
    log,
)

REQUEST_COUNT_FILE = _resolve_path(
    _get_env("VOR_REQUEST_COUNT_FILE"), default=DATA_DIR / "vor_request_count.json"
)


__all__ = [
    "MAX_REQUESTS_PER_DAY",
    "MAX_VOR_QUOTA_FILE_BYTES",
    "REQUEST_COUNT_FILE",
    "_QUOTA_CACHE",
    "_QUOTA_LOCK",
    "_flush_quota_cache",
    "_persist_quota_to_disk",
    "_read_quota_file_unlocked",
    "_reserve_request_slot_locked",
    "_write_request_count_file",
    "file_lock",
    "load_request_count",
    "reserve_request_slot",
    "save_request_count",
]
