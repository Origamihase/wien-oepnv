"""Regression tests for the atomic VAO quota reservation (audit A.2 / A.3).

Audit ``docs/archive/audits/audit-2026-09-13.md`` (§ *Audit Update 19:20*,
findings **A.2** and **A.3**) recorded two defects in the VOR quota gate:

**A.2 — the budget check was not atomic across processes.** The former
``_charge_one_request`` read the counter through ``load_request_count()`` (a
per-process in-memory cache), compared it against ``MAX_REQUESTS_PER_DAY``, and
only then called ``save_request_count``, which buffered the increment in memory
and took the *file* lock later, at flush time. Nothing held a cross-process lock
across check-and-increment, so two runs could both observe ``MAX - 1`` and both
fire a request. The flush-time ``min(..., MAX_REQUESTS_PER_DAY)`` clamp bounded
only the number written to the ledger, never the number of requests actually put
on the wire — it hid the breach rather than preventing it. Reproduced with six
concurrent processes: **104 reservations granted, ledger reported exactly 100.**

**A.3 — the poison sentinel was ineffective on the lock-failure path.** The
inner ``OSError`` branch (write/replace failure) poisoned ``_QUOTA_CACHE``, but
the outer ``except (OSError, TimeoutError)`` merely *returned* ``MAX + 1``. Since
the caller gated on the cache, an unreadable lockfile left the gate open: every
subsequent call re-read an unchanged, low cache value and fired a real request
while nothing was persisted.

These tests pin both fixes. They are deliberately behavioural: the first drives
real concurrent processes rather than asserting on implementation details, so a
future refactor that reintroduces a check-then-act split fails here.
"""

from __future__ import annotations

import json
import multiprocessing
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from zoneinfo import ZoneInfo

import src.providers.vor as vor

VIENNA = ZoneInfo("Europe/Vienna")


def _today_iso() -> str:
    return datetime.now(VIENNA).strftime("%Y-%m-%d")


def _reserve_in_process(
    count_file: str,
    cap: int,
    attempts: int,
    start_event: Any,
    results: Any,
) -> None:
    """Child-process worker: grab as many slots as the budget allows.

    Runs in a ``spawn``ed interpreter, so it re-imports and reloads the module
    with ``VOR_MAX_REQUESTS_PER_DAY`` lowered to the test cap.

    ``REQUEST_COUNT_FILE`` is assigned directly rather than through
    ``VOR_REQUEST_COUNT_FILE``: that env var is routed through ``_resolve_path``,
    which refuses any target outside ``data/`` as path traversal and silently
    falls back to the *production* ledger. Mirrors the existing
    ``_save_request_count_in_process`` helper in ``test_vor_request_limit.py``.
    """
    import importlib
    from pathlib import Path as _Path

    os.environ["VOR_MAX_REQUESTS_PER_DAY"] = str(cap)

    import src.providers.vor as vor_module

    importlib.reload(vor_module)
    vor_module.REQUEST_COUNT_FILE = _Path(count_file)
    assert vor_module.MAX_REQUESTS_PER_DAY == cap

    granted = 0
    start_event.wait()
    for _ in range(attempts):
        ok, _total = vor_module.reserve_request_slot()
        if not ok:
            break
        granted += 1
    results.put(granted)


@pytest.mark.timeout(90)
def test_reserve_request_slot_never_exceeds_cap_across_processes(
    tmp_path: Path,
) -> None:
    """Concurrent processes are granted at most ``MAX_REQUESTS_PER_DAY`` slots.

    This is the A.2 regression. Pre-fix this test observed 104 grants against a
    cap of 100 while the ledger read 100 — the overage was real API traffic the
    counter could not see. The assertion is on *grants* (what would have gone on
    the wire), not merely on the persisted number, because the old clamp already
    kept the persisted number correct.
    """
    count_file = tmp_path / "vor_request_count.json"
    cap = 25
    nproc = 4
    attempts = 20  # 4 * 20 = 80 attempts against a cap of 25

    ctx = multiprocessing.get_context("spawn")
    start_event = ctx.Event()
    results: Any = ctx.Queue()

    processes = [
        ctx.Process(
            target=_reserve_in_process,
            args=(str(count_file), cap, attempts, start_event, results),
        )
        for _ in range(nproc)
    ]
    for proc in processes:
        proc.start()
    start_event.set()

    granted_total = 0
    for _ in range(nproc):
        granted_total += results.get(timeout=60)
    for proc in processes:
        proc.join(30)
        assert not proc.is_alive()
        assert proc.exitcode == 0

    assert granted_total == cap, (
        f"{granted_total} slots granted against a cap of {cap} — the "
        "check-then-increment race is back"
    )
    stored = json.loads(count_file.read_text(encoding="utf-8"))
    assert stored["requests"] == cap
    assert stored["date"] == _today_iso()


def test_reserve_request_slot_refuses_when_budget_spent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An exhausted ledger refuses the reservation without incrementing."""
    count_file = tmp_path / "vor_request_count.json"
    monkeypatch.setattr(vor, "REQUEST_COUNT_FILE", count_file)
    monkeypatch.setitem(vor._QUOTA_CACHE, "date", None)
    monkeypatch.setitem(vor._QUOTA_CACHE, "count", 0)
    monkeypatch.setitem(vor._QUOTA_CACHE, "unsaved_delta", 0)

    count_file.write_text(
        json.dumps({"date": _today_iso(), "requests": vor.MAX_REQUESTS_PER_DAY}),
        encoding="utf-8",
    )

    granted, total = vor.reserve_request_slot()

    assert granted is False
    assert total == vor.MAX_REQUESTS_PER_DAY
    stored = json.loads(count_file.read_text(encoding="utf-8"))
    assert stored["requests"] == vor.MAX_REQUESTS_PER_DAY


def test_reserve_request_slot_grants_the_last_free_slot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The boundary slot (``MAX - 1`` -> ``MAX``) is still handed out."""
    count_file = tmp_path / "vor_request_count.json"
    monkeypatch.setattr(vor, "REQUEST_COUNT_FILE", count_file)
    monkeypatch.setitem(vor._QUOTA_CACHE, "date", None)
    monkeypatch.setitem(vor._QUOTA_CACHE, "count", 0)
    monkeypatch.setitem(vor._QUOTA_CACHE, "unsaved_delta", 0)

    count_file.write_text(
        json.dumps({"date": _today_iso(), "requests": vor.MAX_REQUESTS_PER_DAY - 1}),
        encoding="utf-8",
    )

    granted, total = vor.reserve_request_slot()

    assert granted is True
    assert total == vor.MAX_REQUESTS_PER_DAY
    stored = json.loads(count_file.read_text(encoding="utf-8"))
    assert stored["requests"] == vor.MAX_REQUESTS_PER_DAY

    # ... and the next one is refused.
    assert vor.reserve_request_slot() == (False, vor.MAX_REQUESTS_PER_DAY)


def test_reserve_request_slot_negative_ledger_cannot_widen_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A tampered negative count is clamped at 0, not treated as headroom."""
    count_file = tmp_path / "vor_request_count.json"
    monkeypatch.setattr(vor, "REQUEST_COUNT_FILE", count_file)
    monkeypatch.setitem(vor._QUOTA_CACHE, "date", None)
    monkeypatch.setitem(vor._QUOTA_CACHE, "count", 0)
    monkeypatch.setitem(vor._QUOTA_CACHE, "unsaved_delta", 0)

    count_file.write_text(
        json.dumps({"date": _today_iso(), "requests": -5_000}), encoding="utf-8"
    )

    granted, total = vor.reserve_request_slot()

    assert granted is True
    assert total == 1, "negative ledger must clamp to 0 before the increment"


def test_reserve_request_slot_boolean_ledger_is_not_credited(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``true`` in the ledger must not be read as ``int(True) == 1``."""
    count_file = tmp_path / "vor_request_count.json"
    monkeypatch.setattr(vor, "REQUEST_COUNT_FILE", count_file)
    monkeypatch.setitem(vor._QUOTA_CACHE, "date", None)
    monkeypatch.setitem(vor._QUOTA_CACHE, "count", 0)
    monkeypatch.setitem(vor._QUOTA_CACHE, "unsaved_delta", 0)

    count_file.write_text(
        json.dumps({"date": _today_iso(), "requests": True}), encoding="utf-8"
    )

    assert vor._read_quota_file_unlocked() == (_today_iso(), 0)


def test_lock_failure_refuses_and_poisons_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A.3: an unusable lock fails closed AND poisons the in-process cache.

    Pre-fix the outer handler only returned the sentinel; the cache kept its
    old, low value so the next cache-reading gate waved the call through.
    """
    count_file = tmp_path / "vor_request_count.json"
    monkeypatch.setattr(vor, "REQUEST_COUNT_FILE", count_file)
    monkeypatch.setitem(vor._QUOTA_CACHE, "date", None)
    monkeypatch.setitem(vor._QUOTA_CACHE, "count", 0)
    monkeypatch.setitem(vor._QUOTA_CACHE, "unsaved_delta", 0)

    count_file.write_text(
        json.dumps({"date": _today_iso(), "requests": 7}), encoding="utf-8"
    )

    @contextmanager
    def failing_lock(*args: Any, **kwargs: Any) -> Iterator[None]:
        raise OSError("boom")
        yield  # type: ignore[unreachable]

    monkeypatch.setattr(vor, "file_lock", failing_lock)

    granted, total = vor.reserve_request_slot()

    assert granted is False
    assert total == vor.MAX_REQUESTS_PER_DAY + 1
    # The cache is poisoned, so a cache-reading gate also refuses.
    assert vor._QUOTA_CACHE["count"] == vor.MAX_REQUESTS_PER_DAY + 1
    assert vor._QUOTA_CACHE["unsaved_delta"] == 0
    _date, usage = vor.load_request_count()
    assert usage > vor.MAX_REQUESTS_PER_DAY
    # The ledger itself is untouched.
    stored = json.loads(count_file.read_text(encoding="utf-8"))
    assert stored["requests"] == 7


def test_write_failure_refuses_and_leaves_ledger_untouched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failed persist must not hand out the slot it could not record."""
    count_file = tmp_path / "vor_request_count.json"
    monkeypatch.setattr(vor, "REQUEST_COUNT_FILE", count_file)
    monkeypatch.setitem(vor._QUOTA_CACHE, "date", None)
    monkeypatch.setitem(vor._QUOTA_CACHE, "count", 0)
    monkeypatch.setitem(vor._QUOTA_CACHE, "unsaved_delta", 0)

    count_file.write_text(
        json.dumps({"date": _today_iso(), "requests": 3}), encoding="utf-8"
    )

    def failing_replace(src: Any, dst: Any) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", failing_replace)

    granted, total = vor.reserve_request_slot()

    assert granted is False
    assert total == vor.MAX_REQUESTS_PER_DAY + 1
    assert vor._QUOTA_CACHE["count"] == vor.MAX_REQUESTS_PER_DAY + 1
    stored = json.loads(count_file.read_text(encoding="utf-8"))
    assert stored["requests"] == 3


def test_charge_one_request_raises_when_reservation_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_charge_one_request`` fails closed on every refusal reason."""
    import scripts.update_stammstrecke_status as status

    # ``status.vor_provider`` IS ``src.providers.vor``; patch the module itself
    # so the lookup the script performs at call time sees the stub.
    monkeypatch.setattr(
        vor, "reserve_request_slot", lambda _now=None: (False, vor.MAX_REQUESTS_PER_DAY)
    )
    with pytest.raises(status._QuotaExceeded):
        status._charge_one_request(datetime.now(VIENNA))

    # Sentinel refusal (lock/write failure) must raise too, not slip through.
    monkeypatch.setattr(
        vor,
        "reserve_request_slot",
        lambda _now=None: (False, vor.MAX_REQUESTS_PER_DAY + 1),
    )
    with pytest.raises(status._QuotaExceeded):
        status._charge_one_request(datetime.now(VIENNA))


def test_charge_one_request_passes_when_granted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A granted reservation returns without raising."""
    import scripts.update_stammstrecke_status as status

    monkeypatch.setattr(vor, "reserve_request_slot", lambda _now=None: (True, 1))
    status._charge_one_request(datetime.now(VIENNA))


def test_save_request_count_still_persists_every_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The compat wrapper keeps its contract and no longer defers the write.

    The batched shape only persisted every ``QUOTA_FLUSH_BATCH_SIZE`` calls,
    which is what allowed an unflushed delta to be lost on SIGKILL. Each call
    now lands on disk immediately.
    """
    count_file = tmp_path / "vor_request_count.json"
    monkeypatch.setattr(vor, "REQUEST_COUNT_FILE", count_file)
    monkeypatch.setitem(vor._QUOTA_CACHE, "date", None)
    monkeypatch.setitem(vor._QUOTA_CACHE, "count", 0)
    monkeypatch.setitem(vor._QUOTA_CACHE, "unsaved_delta", 0)

    for expected in (1, 2, 3):
        assert vor.save_request_count() == expected
        stored = json.loads(count_file.read_text(encoding="utf-8"))
        assert stored["requests"] == expected, "write was deferred, not atomic"
