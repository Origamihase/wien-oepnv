"""Cross-platform file locking utilities."""

from __future__ import annotations

import errno
import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator

try:  # pragma: no cover - platform dependent
    import fcntl  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    fcntl = None  # type: ignore

try:  # pragma: no cover - platform dependent
    import msvcrt  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    msvcrt = None  # type: ignore

log = logging.getLogger(__name__)


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

__all__ = ["file_lock"]
