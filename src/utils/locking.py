"""Cross-platform file locking utilities."""

from __future__ import annotations

import errno
import logging
import os
import threading
from contextlib import contextmanager
from typing import Any, Iterator, MutableMapping

try:  # pragma: no cover - platform dependent
    import fcntl  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    fcntl = None  # type: ignore

try:  # pragma: no cover - platform dependent
    import msvcrt  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    msvcrt = None  # type: ignore

log = logging.getLogger(__name__)

# Global registry of thread locks for file paths to ensure process-local thread safety
_THREAD_LOCKS: MutableMapping[str, threading.Lock] = {}
_LOCK_COUNTS: MutableMapping[str, int] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def _acquire_thread_lock_ref(path: str) -> threading.Lock:
    """Retrieve or create a threading.Lock and increment reference count."""
    with _THREAD_LOCKS_GUARD:
        if path not in _THREAD_LOCKS:
            _THREAD_LOCKS[path] = threading.Lock()
            _LOCK_COUNTS[path] = 0
        _LOCK_COUNTS[path] += 1
        return _THREAD_LOCKS[path]


def _release_thread_lock_ref(path: str) -> None:
    """Decrement reference count and remove lock if unused."""
    with _THREAD_LOCKS_GUARD:
        _LOCK_COUNTS[path] -= 1
        if _LOCK_COUNTS[path] <= 0:
            if path in _THREAD_LOCKS:
                del _THREAD_LOCKS[path]
            if path in _LOCK_COUNTS:
                del _LOCK_COUNTS[path]


def _lock_length(fileobj: Any) -> int:
    try:
        fileno = fileobj.fileno()
    except (AttributeError, OSError):
        return 1

    try:
        size = os.fstat(fileno).st_size
    except OSError:
        try:
            current = fileobj.tell()
            fileobj.seek(0, os.SEEK_END)
            size = fileobj.tell()
            fileobj.seek(current, os.SEEK_SET)
        except Exception:
            return 1
    return max(int(size), 1)


def _acquire_file_lock(fileobj: Any, exclusive: bool) -> None:
    if fcntl is not None:  # pragma: no branch - simple POSIX case
        flag = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        while True:
            try:
                fcntl.flock(fileobj.fileno(), flag)
                return
            except OSError as exc:  # pragma: no cover - rare EINTR handling
                if exc.errno != errno.EINTR:
                    raise
    elif msvcrt is not None:  # pragma: no cover - Windows fallback
        length = _lock_length(fileobj)
        shared_flag = getattr(msvcrt, "LK_RLCK", getattr(msvcrt, "LK_LOCK"))
        mode = msvcrt.LK_LOCK if exclusive else shared_flag
        current = None
        try:
            current = fileobj.tell()
        except Exception:
            current = None
        fileobj.seek(0)
        try:
            msvcrt.locking(fileobj.fileno(), mode, length)
        finally:
            if current is not None:
                fileobj.seek(current)


def _release_file_lock(fileobj: Any) -> None:
    if fcntl is not None:  # pragma: no branch - simple POSIX case
        while True:
            try:
                fcntl.flock(fileobj.fileno(), fcntl.LOCK_UN)
                return
            except OSError as exc:  # pragma: no cover - rare EINTR handling
                if exc.errno != errno.EINTR:
                    raise
    elif msvcrt is not None:  # pragma: no cover - Windows fallback
        length = _lock_length(fileobj)
        unlock_flag = getattr(msvcrt, "LK_UNLCK", getattr(msvcrt, "LK_UNLOCK", None))
        if unlock_flag is None:  # pragma: no cover - extremely unlikely
            return
        current = None
        try:
            current = fileobj.tell()
        except Exception:
            current = None
        fileobj.seek(0)
        try:
            msvcrt.locking(fileobj.fileno(), unlock_flag, length)
        finally:
            if current is not None:
                fileobj.seek(current)


@contextmanager
def file_lock(fileobj: Any, *, exclusive: bool) -> Iterator[None]:
    """Context manager for acquiring a cross-platform file lock."""
    # Step 1: Thread-level locking
    thread_lock = None
    path = None
    try:
        if hasattr(fileobj, "name"):
            path = os.path.abspath(fileobj.name)
            thread_lock = _acquire_thread_lock_ref(path)
            thread_lock.acquire()
    except Exception as exc:
        log.warning("Could not acquire thread lock for file %s: %s", getattr(fileobj, "name", "unknown"), exc)

    # Step 2: OS-level locking
    locked = False
    try:
        _acquire_file_lock(fileobj, exclusive)
        locked = True
    except Exception as exc:  # pragma: no cover - lock failures are rare
        log.debug("Dateisperre fehlgeschlagen (%s) – fahre ohne Lock fort.", exc)
    try:
        yield
    finally:
        if locked:
            try:
                _release_file_lock(fileobj)
            except Exception as exc:  # pragma: no cover - release failures are rare
                log.debug("Dateisperre konnte nicht gelöst werden: %s", exc)

        if thread_lock:
            thread_lock.release()
            if path:
                _release_thread_lock_ref(path)

__all__ = ["file_lock"]
