"""Regression tests for the thread-lock reference counting in ``file_lock``.

Historically, when ``threading.Lock.acquire()`` raised an exception during
``file_lock`` setup (e.g. because the call was interrupted), the per-path
reference counter incremented by ``_acquire_thread_lock_ref`` was leaked: the
``finally`` block then attempted to release a lock that had never been
acquired, the resulting :class:`RuntimeError` propagated, and
``_release_thread_lock_ref`` was never invoked. The leak prevented the lock
entry from ever being garbage-collected from ``_THREAD_LOCKS`` for the lifetime
of the process.
"""

from __future__ import annotations

from typing import Any

from src.utils import locking


class _BadLock:
    """A drop-in for :class:`threading.Lock` whose ``acquire`` always raises."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc
        self.released = False

    def acquire(self, *_args: Any, **_kwargs: Any) -> bool:
        raise self._exc

    def release(self) -> None:  # pragma: no cover - must not be called
        self.released = True
        raise AssertionError(
            "release() must not run when acquire() raised"
        )


class _Fakefile:
    """Minimal stand-in for an open file object that ``file_lock`` accepts."""

    def __init__(self, path: str) -> None:
        self.name = path

    def fileno(self) -> int:  # pragma: no cover - never reached in these tests
        raise OSError("no real file descriptor")


def _snapshot_state() -> tuple[dict[str, Any], dict[str, int]]:
    return dict(locking._THREAD_LOCKS), dict(locking._LOCK_COUNTS)


def test_thread_lock_counter_balanced_when_acquire_raises(
    tmp_path, monkeypatch
) -> None:
    """If ``threading.Lock.acquire`` raises, the reference counter must reset."""

    target = tmp_path / "leaky.lock"
    fake_file = _Fakefile(str(target))

    locks_before, counts_before = _snapshot_state()

    bad_lock = _BadLock(RuntimeError("simulated acquire failure"))

    def fake_acquire_ref(path: str):
        # Reproduce the side effects that the production helper has: bump the
        # counter and register the lock so we can verify the cleanup path.
        with locking._THREAD_LOCKS_GUARD:
            locking._THREAD_LOCKS[path] = bad_lock  # type: ignore[assignment]
            locking._LOCK_COUNTS[path] = (
                locking._LOCK_COUNTS.get(path, 0) + 1
            )
        return bad_lock

    monkeypatch.setattr(locking, "_acquire_thread_lock_ref", fake_acquire_ref)

    # ``file_lock`` should still enter and exit cleanly even when the thread
    # lock acquire fails.
    with locking.file_lock(fake_file, exclusive=True, timeout=0.1):
        pass

    locks_after, counts_after = _snapshot_state()
    assert locks_after == locks_before, (
        "Thread lock dictionary leaked an entry after acquire() failure"
    )
    assert counts_after == counts_before, (
        "Reference counter leaked after acquire() failure"
    )
    assert not bad_lock.released, (
        "release() must not be invoked when acquire() raised"
    )


def test_thread_lock_counter_balanced_when_acquire_raises_baseexception(
    tmp_path, monkeypatch
) -> None:
    """``KeyboardInterrupt`` during acquire must not leak the reference counter."""

    target = tmp_path / "interrupt.lock"
    fake_file = _Fakefile(str(target))

    locks_before, counts_before = _snapshot_state()

    bad_lock = _BadLock(KeyboardInterrupt())

    def fake_acquire_ref(path: str):
        with locking._THREAD_LOCKS_GUARD:
            locking._THREAD_LOCKS[path] = bad_lock  # type: ignore[assignment]
            locking._LOCK_COUNTS[path] = (
                locking._LOCK_COUNTS.get(path, 0) + 1
            )
        return bad_lock

    monkeypatch.setattr(locking, "_acquire_thread_lock_ref", fake_acquire_ref)

    try:
        with locking.file_lock(fake_file, exclusive=True, timeout=0.1):
            pass  # pragma: no cover - acquire raises before yield
    except KeyboardInterrupt:
        pass

    locks_after, counts_after = _snapshot_state()
    assert locks_after == locks_before
    assert counts_after == counts_before
    assert not bad_lock.released


def test_thread_lock_counter_balanced_on_success(tmp_path) -> None:
    """The happy path must continue to balance acquire/release."""

    target = tmp_path / "ok.lock"
    target.touch()
    locks_before, counts_before = _snapshot_state()

    with target.open("a+", encoding="utf-8") as fh:
        with locking.file_lock(fh, exclusive=True, timeout=0.1):
            pass

    locks_after, counts_after = _snapshot_state()
    assert locks_after == locks_before
    assert counts_after == counts_before
