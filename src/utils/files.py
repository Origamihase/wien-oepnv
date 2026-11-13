"""File utility helpers."""
# Atomic writing (``atomic_write``) and the size-capped readers
# (``read_capped_json`` / ``read_capped_text`` / ``read_capped_bytes``) live
# here; the rest is split by topic and re-exported:
#
# * :mod:`src.utils.files_paths` - safe path joining, safe file names, hash,
# * :mod:`src.utils.files_json` - strict JSON parsing (no NaN / Infinity),
# * :mod:`src.utils.files_zip` - ZIP archive validation and its caps.
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any
from collections.abc import Iterator

from .files_json import (
    _reject_non_finite_constant,
    _reject_non_finite_float,
    loads_finite,
)
from .files_paths import get_file_hash, safe_path_join, sanitize_filename
from .files_zip import (
    DEFAULT_MAX_ZIP_ENTRIES,
    DEFAULT_MAX_ZIP_FILENAME_LENGTH,
    DEFAULT_MAX_ZIP_PER_ENTRY_UNCOMPRESSED,
    DEFAULT_MAX_ZIP_TOTAL_UNCOMPRESSED,
    validate_zip_archive_safe,
)

__all__ = [
    "DEFAULT_MAX_BYTES_FILE_BYTES",
    "DEFAULT_MAX_JSON_FILE_BYTES",
    "DEFAULT_MAX_TEXT_FILE_BYTES",
    "DEFAULT_MAX_ZIP_ENTRIES",
    "DEFAULT_MAX_ZIP_FILENAME_LENGTH",
    "DEFAULT_MAX_ZIP_PER_ENTRY_UNCOMPRESSED",
    "DEFAULT_MAX_ZIP_TOTAL_UNCOMPRESSED",
    "_reject_non_finite_constant",
    "_reject_non_finite_float",
    "atomic_write",
    "get_file_hash",
    "loads_finite",
    "read_capped_bytes",
    "read_capped_json",
    "read_capped_text",
    "safe_path_join",
    "sanitize_filename",
    "validate_zip_archive_safe",
]

# Default per-loader byte cap for on-disk JSON files. Sized at ~100x the
# largest legitimately-written stations.json (~175 KiB) and polygon
# (~146 KiB) shapes, comfortably below any cron runner's 1 GiB cgroup
# limit. Callers requiring a different ceiling (e.g. a multi-MiB GTFS
# manifest) pass an explicit ``max_bytes``; the default keeps the
# canonical defence available without per-site bikeshedding.
DEFAULT_MAX_JSON_FILE_BYTES = 50 * 1024 * 1024

# Canonical default cap for non-JSON text payloads (CSV, .env, log files)
# read into memory in one shot. Sized identically to the JSON cap so the
# two helpers share the same threat-model bound. Callers requiring a
# tighter ceiling (small mapping CSV, single-line secret) pass an explicit
# ``max_bytes``.
DEFAULT_MAX_TEXT_FILE_BYTES = 50 * 1024 * 1024

# Canonical default cap for binary blob payloads (XLSX workbooks, ZIP
# archives, image / PDF caches) read into memory in one shot via
# :func:`read_capped_bytes`. Sized identically to the JSON / text caps so
# the three helpers share the same threat-model bound — a planted-huge
# binary file (compromised CI runner / partial flush + power loss /
# parallel orchestrator atomic state swap) buffered via
# ``Path.read_bytes()`` allocates O(file_size) bytes and raises
# ``MemoryError`` (a ``BaseException`` subclass NOT caught by
# ``except OSError``) past the surrounding handler and crashes the cron
# pipeline (the orchestrator runs every update script via
# ``subprocess.run(check=True)``). Callers requiring a tighter ceiling
# (XLSX cache pinned at the HTTP fetch cap, small binary blob) pass an
# explicit ``max_bytes``.
DEFAULT_MAX_BYTES_FILE_BYTES = 50 * 1024 * 1024


def _close_and_cleanup_failed_write(
    f: IO[Any] | None, fd: int | None, tmp_path: Path
) -> None:
    """Best-effort cleanup after a failed :func:`atomic_write`.

    Closes whichever descriptor is still open — the file object when
    ``open(fd, ...)`` succeeded, otherwise the raw ``fd`` from ``os.open``
    (which would leak if ``open(fd, ...)`` raised before taking ownership) —
    then removes the temporary file.
    """
    log = logging.getLogger(__name__)
    # Close if still open (e.g. exception during yield)
    if f is not None:
        try:
            f.close()
        except Exception as close_exc:
            log.warning("Failed to close temporary file", exc_info=close_exc)
    elif fd is not None:
        # ``os.open`` succeeded but ``open(fd, ...)`` never took ownership
        # (e.g. an invalid ``encoding`` raises LookupError before the file
        # object is built). Close the raw descriptor so it does not leak.
        try:
            os.close(fd)
        except OSError:
            pass  # already closed or invalid; nothing left to release
    # Cleanup temp file
    if os.path.exists(tmp_path):
        try:
            os.unlink(tmp_path)
        except OSError as unlink_exc:
            log.warning("Failed to remove temporary file", exc_info=unlink_exc)


@contextmanager
def atomic_write(
    path: str | Path,
    mode: str = "w",
    encoding: str | None = "utf-8",
    permissions: int = 0o644,
    newline: str | None = None,
    overwrite: bool = True,
) -> Iterator[IO[Any]]:
    """Safe atomic file write using a temporary file.

    Args:
        path: Target file path.
        mode: Open mode ('w' for text, 'wb' for binary).
        encoding: Text encoding (default: 'utf-8'). Ignored if binary mode.
        permissions: File permissions (default: 0o644 for public readable).
                     Use 0o600 for secrets/internal caches.
        newline: Newline control (passed to open).
        overwrite: If False, raises FileExistsError if target exists.
    """
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    if not overwrite and target.exists():
        raise FileExistsError(f"File {target} already exists")

    text_mode = "b" not in mode
    if not text_mode:
        encoding = None
        newline = None

    # Generate a unique temporary filename with cryptographically secure random token to ensure no collision
    # and prevent "orphaned file blocking" issues if the process crashes.
    unique_id = secrets.token_hex(16)
    tmp_path = target.with_name(f"{target.name}.{unique_id}.tmp")

    fd: int | None = None
    f: IO[Any] | None = None
    try:
        flags = os.O_CREAT | os.O_EXCL
        if "a" in mode:
            flags |= os.O_APPEND
        if "+" in mode:
            flags |= os.O_RDWR
        elif "r" in mode:
            flags |= os.O_RDONLY
        else:
            flags |= os.O_WRONLY

        fd = os.open(tmp_path, flags, 0o600)
        # Security: Immediately enforce restrictive permissions bypassing umask
        try:
            os.fchmod(fd, 0o600)
        except OSError:
            pass  # no fchmod on this platform (Windows); os.open already used 0o600

        f = open(fd, mode, encoding=encoding, newline=newline)
        # The file object now owns the descriptor — closing ``f`` closes
        # ``fd``. Null the raw handle so the failure path never double-closes
        # it (a re-used fd could by then belong to an unrelated file).
        fd = None
        # ``with f`` closes the temporary file before it is moved into place,
        # on success and on error alike; the failure path below may close it
        # once more, which is a no-op on an already closed file object.
        with f:
            yield f
            f.flush()
            os.fsync(f.fileno())

            # Set permissions before moving into place and closing
            try:
                os.fchmod(f.fileno(), permissions)
            except OSError:
                pass  # no fchmod on this platform (Windows); the file keeps its 0o600

        if overwrite:
            os.replace(tmp_path, target)
        else:
            # Security: Use os.link to prevent TOCTOU race condition
            # If target was created between our initial check and now, os.link will fail.
            try:
                os.link(tmp_path, target)
                # Hard link successful, now remove the temp file
                os.unlink(tmp_path)
            except FileExistsError as exc:
                raise FileExistsError(f"File {target} already exists") from exc

        # Durability: the file's data blocks were fsync'd above, but the
        # directory entry created by the rename/link is a separate metadata
        # write that can be lost on power loss / kernel crash. fsync the parent
        # directory so the appearance of ``target`` is itself durable. Best
        # effort: platforms without directory file descriptors (e.g. Windows)
        # raise ``OSError`` on ``os.open`` of a directory and degrade to a
        # no-op, matching the os.fchmod handling above.
        try:
            dir_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass  # no directory descriptors on this platform (Windows); see comment above

    except BaseException:
        # BaseException, not Exception: a KeyboardInterrupt or SystemExit
        # raised inside the ``with`` body must still close the temporary
        # file and remove it instead of leaking both.
        _close_and_cleanup_failed_write(f, fd, tmp_path)
        raise


def read_capped_json(
    path: Path,
    max_bytes: int = DEFAULT_MAX_JSON_FILE_BYTES,
    *,
    label: str = "JSON",
    logger: logging.Logger | None = None,
) -> object | None:
    """Read JSON from *path*, returning ``None`` if missing/invalid/oversized.

    Combines the canonical size-bomb defence with the depth-bomb catch tuple
    ``(OSError, json.JSONDecodeError, RecursionError)``. The byte-size cap is
    enforced via ``os.fstat`` on the *open file descriptor* AND a defensive
    ``read(max_bytes + 1)`` budget — closing the TOCTOU window between
    ``Path.stat`` and ``Path.open`` that lets an attacker swap the inode
    (atomic ``os.replace`` of a symlink, parallel writer's ``atomic_write``
    rename) under the loader's feet.

    Threat model: a planted-huge JSON file (compromised CI runner / partial
    flush + power loss / corrupted previous run / parallel orchestrator
    process performing an atomic state swap mid-read) buffered into memory
    via ``json.load(handle)`` allocates O(file_size) bytes plus a multiplier
    of object overhead, exhausts the runner's cgroup memory limit, and
    propagates ``MemoryError`` (a ``BaseException`` subclass that is NOT
    caught by ``except (OSError, json.JSONDecodeError, RecursionError)``)
    past the loader to crash the cron pipeline.

    TOCTOU shape closed: pre-fix ``Path.stat()`` and ``Path.open()`` resolve
    the path AND follow symlinks INDEPENDENTLY. An attacker who can swap the
    inode at *path* between those two syscalls (``os.replace`` is atomic at
    the directory-entry level) bypasses the cap: stat sees the small target,
    open opens the swapped huge target, and ``json.load`` buffers the huge
    file. Post-fix ``os.fstat(handle.fileno())`` reports the size of the
    *opened* inode, immune to subsequent symlink swaps; the additional
    ``handle.read(max_bytes + 1)`` cap defends against special files
    (FIFOs, ``/dev/zero``, character devices) that report ``st_size == 0``
    but yield unbounded bytes on read.
    """
    log = logger if logger is not None else logging.getLogger(__name__)
    # Security: emit a fingerprint of the path rather than the path
    # itself. ``path`` is a caller-controlled value reachable from
    # ``read_secret(name=...)`` in ``src/utils/env.py``; CodeQL's
    # ``py/clear-text-logging-sensitive-data`` taint analysis marks
    # the entire dataflow as secret-bearing because the helper is
    # transitively called with credential-coded parameter names.
    # ``sanitize_log_arg(str(path))`` is NOT recognised as a barrier
    # across the function boundary. Logging a one-way hash of the path
    # bytes (a) keeps an operator-correlatable fingerprint without
    # exposing the path string, (b) is recognised as a CodeQL barrier
    # via ``hashlib`` taint sinks, and (c) avoids leaking
    # Trojan-Source / control-character / ANSI-escape primitives a
    # hostile path name might carry. Operators rerun the hash on a
    # candidate path locally to verify identity.
    path_fingerprint = hashlib.sha256(
        str(path).encode("utf-8", errors="replace")
    ).hexdigest()[:12]
    try:
        # Open first so the size check is on the actual inode that
        # ``read()`` will consume — closes the stat/open TOCTOU.
        with path.open("rb") as handle:
            if os.fstat(handle.fileno()).st_size > max_bytes:
                log.warning(
                    "%s file [path-sha256=%s] is too large (> %d bytes); treating as missing.",
                    label, path_fingerprint, max_bytes,
                )
                return None
            # Defense in depth: bound the read at ``max_bytes + 1`` so a
            # special file with ``st_size == 0`` (FIFO, ``/dev/zero``,
            # character device) cannot stream unbounded bytes into
            # ``json.loads``.
            raw = handle.read(max_bytes + 1)
            if len(raw) > max_bytes:
                log.warning(
                    "%s file [path-sha256=%s] exceeded %d bytes during read; treating as missing.",
                    label, path_fingerprint, max_bytes,
                )
                return None
            # Security: ``parse_constant`` + ``parse_float`` hooks reject
            # the canonical non-finite literal family (``NaN`` /
            # ``Infinity`` / ``-Infinity`` tokens + scientific-notation
            # overflow ``1e1000`` → ``+inf``). Mirrors the writer-side
            # ``allow_nan=False`` pin from Round 1485 / 1487 / 1488 so a
            # planted on-disk literal does NOT propagate as
            # ``float('nan')`` / ``float('inf')`` into Python computation
            # and does NOT round-trip back to the writer where it would
            # hit ``allow_nan=False`` and crash the cron pipeline.
            payload: object = json.loads(
                raw,
                parse_constant=_reject_non_finite_constant,
                parse_float=_reject_non_finite_float,
            )
            return payload
    except (OSError, json.JSONDecodeError, RecursionError, UnicodeDecodeError):
        return None


def read_capped_text(
    path: Path,
    max_bytes: int = DEFAULT_MAX_TEXT_FILE_BYTES,
    *,
    encoding: str = "utf-8",
    errors: str = "strict",
    label: str = "text",
    logger: logging.Logger | None = None,
) -> str | None:
    """Read text from *path*, returning ``None`` if missing/oversized/invalid.

    Mirrors :func:`read_capped_json` for non-JSON text payloads (CSV,
    log files, .env files) where ``Path.read_text()`` would buffer the
    entire file before processing — a ``BaseException``-rooted
    ``MemoryError`` that propagates past surrounding ``except OSError``
    handlers and crashes the cron pipeline.

    Threat model: identical to :func:`read_capped_json`. A planted-huge
    file at *path* (compromised CI runner / partial flush + power loss /
    corrupted previous run / parallel orchestrator process performing an
    atomic state swap mid-read) buffered into memory via
    ``Path.read_text()`` allocates O(file_size) bytes and raises
    ``MemoryError``. The fix shape mirrors ``read_capped_json``: open
    first, fstat the open file descriptor (TOCTOU-safe), then bound the
    read at ``max_bytes + 1`` (special-file safe).

    The optional *errors* parameter mirrors :func:`bytes.decode` /
    :func:`Path.read_text` — pass ``errors="ignore"`` for callers that
    previously consumed lossy text (e.g. the secret scanner walking
    every tracked file in the repo, where non-UTF-8 fragments must not
    drop the whole file).
    """
    log = logger if logger is not None else logging.getLogger(__name__)
    # Security: see ``read_capped_json`` for the rationale of fingerprinting
    # ``path`` instead of interpolating it. Same CodeQL clear-text-logging
    # surface, same Trojan-Source / control-character defanging surface,
    # same hashlib-based barrier shape.
    path_fingerprint = hashlib.sha256(
        str(path).encode("utf-8", errors="replace")
    ).hexdigest()[:12]
    try:
        # Open first so the size check is on the actual inode that
        # ``read()`` will consume — closes the stat/open TOCTOU.
        with path.open("rb") as handle:
            if os.fstat(handle.fileno()).st_size > max_bytes:
                log.warning(
                    "%s file [path-sha256=%s] is too large (> %d bytes); treating as missing.",
                    label, path_fingerprint, max_bytes,
                )
                return None
            # Defense in depth: bound the read at ``max_bytes + 1`` so a
            # special file with ``st_size == 0`` (FIFO, ``/dev/zero``,
            # character device) cannot stream unbounded bytes.
            raw = handle.read(max_bytes + 1)
            if len(raw) > max_bytes:
                log.warning(
                    "%s file [path-sha256=%s] exceeded %d bytes during read; treating as missing.",
                    label, path_fingerprint, max_bytes,
                )
                return None
            return raw.decode(encoding, errors=errors)
    except (OSError, UnicodeDecodeError):
        return None


def read_capped_bytes(
    path: Path,
    max_bytes: int = DEFAULT_MAX_BYTES_FILE_BYTES,
    *,
    label: str = "bytes",
    logger: logging.Logger | None = None,
) -> bytes | None:
    """Read raw bytes from *path*, returning ``None`` if missing/oversized.

    Mirrors :func:`read_capped_json` / :func:`read_capped_text` for binary
    blob payloads (XLSX workbooks, ZIP archives, cached image / PDF blobs)
    where ``Path.read_bytes()`` would buffer the entire file into memory
    before any downstream defence layer can run — a ``BaseException``-
    rooted ``MemoryError`` that propagates past surrounding
    ``except OSError`` handlers and crashes the cron pipeline (the
    orchestrator runs every update script via
    ``subprocess.run(check=True)``).

    Threat model: identical to :func:`read_capped_json`. A planted-huge
    binary file at *path* (compromised CI runner / partial flush + power
    loss / corrupted previous run / parallel orchestrator process
    performing an atomic state swap mid-read) buffered into memory via
    ``Path.read_bytes()`` allocates O(file_size) bytes and raises
    ``MemoryError``. The fix shape mirrors :func:`read_capped_json`:
    open first, fstat the open file descriptor (TOCTOU-safe), then bound
    the read at ``max_bytes + 1`` (special-file safe — defends against
    FIFOs, ``/dev/zero``, character devices that report
    ``st_size == 0`` but yield unbounded bytes on read).
    """
    log = logger if logger is not None else logging.getLogger(__name__)
    # Security: see ``read_capped_json`` for the rationale of fingerprinting
    # ``path`` instead of interpolating it. Same CodeQL clear-text-logging
    # surface, same Trojan-Source / control-character defanging surface,
    # same hashlib-based barrier shape.
    path_fingerprint = hashlib.sha256(
        str(path).encode("utf-8", errors="replace")
    ).hexdigest()[:12]
    try:
        # Open first so the size check is on the actual inode that
        # ``read()`` will consume — closes the stat/open TOCTOU.
        with path.open("rb") as handle:
            if os.fstat(handle.fileno()).st_size > max_bytes:
                log.warning(
                    "%s file [path-sha256=%s] is too large (> %d bytes); treating as missing.",
                    label, path_fingerprint, max_bytes,
                )
                return None
            # Defense in depth: bound the read at ``max_bytes + 1`` so a
            # special file with ``st_size == 0`` (FIFO, ``/dev/zero``,
            # character device) cannot stream unbounded bytes.
            raw = handle.read(max_bytes + 1)
            if len(raw) > max_bytes:
                log.warning(
                    "%s file [path-sha256=%s] exceeded %d bytes during read; treating as missing.",
                    label, path_fingerprint, max_bytes,
                )
                return None
            return raw
    except OSError:
        return None
