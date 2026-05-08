"""File utility helpers."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any
from collections.abc import Iterator

# Default per-loader byte cap for on-disk JSON files. Sized at ~100x the
# largest legitimately-written stations.json (~175 KiB) and polygon
# (~146 KiB) shapes, comfortably below any cron runner's 1 GiB cgroup
# limit. Callers requiring a different ceiling (e.g. a multi-MiB GTFS
# manifest) pass an explicit ``max_bytes``; the default keeps the
# canonical defence available without per-site bikeshedding.
DEFAULT_MAX_JSON_FILE_BYTES = 50 * 1024 * 1024


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
            pass

        f = open(fd, mode, encoding=encoding, newline=newline)
        yield f
        f.flush()
        os.fsync(f.fileno())

        # Set permissions before moving into place and closing
        try:
            os.fchmod(fd, permissions)
        except OSError:
            pass

        f.close()
        f = None  # Prevent double close in finally

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

    except Exception:
        # Close if still open (e.g. exception during yield)
        if f is not None:
            try:
                f.close()
            except Exception as close_exc:
                import logging
                logging.getLogger(__name__).warning("Failed to close temporary file", exc_info=close_exc)
        # Cleanup temp file
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError as unlink_exc:
                import logging
                logging.getLogger(__name__).warning("Failed to remove temporary file", exc_info=unlink_exc)
        raise


def safe_path_join(base: str | Path, *paths: str | Path) -> Path:
    """Safely join paths, ensuring the result is within the base directory."""
    base_abs = os.path.abspath(base)

    # Check for direct path traversal attempts in the inputs
    for p in paths:
        if '..' in str(p):
            raise ValueError(f"Path traversal detected in '{p}'")

    # os.path.join with an absolute path arg resets the path.
    # We must prevent absolute path bypass.
    for p in paths:
        if os.path.isabs(p):
            raise ValueError(f"Absolute path bypass detected in '{p}'")

    final_path = os.path.abspath(os.path.join(base_abs, *paths))

    # Ensure base_abs has a trailing separator for exact containment check
    # so that "/var/lib/cache_dir" doesn't mistakenly contain "/var/lib/cache_dir_evil"
    base_check = base_abs if base_abs.endswith(os.sep) else base_abs + os.sep

    if not final_path.startswith(base_check) and final_path != base_abs:
        raise ValueError(f"Path escape detected: {final_path} is outside of {base_abs}")

    return Path(final_path)


def sanitize_filename(filename_id: str) -> str:
    """Sanitize a filename ID to prevent path traversal."""
    # Only allow alphanumeric characters, dashes, and underscores
    safe_base = re.sub(r'[^a-zA-Z0-9_-]', '_', str(filename_id))
    # Verified: Uses SHA256 (no MD5) for security linter compliance
    id_hash = hashlib.sha256(str(filename_id).encode('utf-8')).hexdigest()[:6]
    return f"{safe_base}_{id_hash}"


def get_file_hash(filepath: str | Path, chunk_size: int = 4096) -> str:
    """Calculate the SHA256 hash of a file using chunked reading to minimize memory footprint."""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


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
    try:
        # Open first so the size check is on the actual inode that
        # ``read()`` will consume — closes the stat/open TOCTOU.
        with path.open("rb") as handle:
            if os.fstat(handle.fileno()).st_size > max_bytes:
                log.warning(
                    "%s file at %s is too large (> %d bytes); treating as missing.",
                    label, path, max_bytes,
                )
                return None
            # Defense in depth: bound the read at ``max_bytes + 1`` so a
            # special file with ``st_size == 0`` (FIFO, ``/dev/zero``,
            # character device) cannot stream unbounded bytes into
            # ``json.loads``.
            raw = handle.read(max_bytes + 1)
            if len(raw) > max_bytes:
                log.warning(
                    "%s file at %s exceeded %d bytes during read; treating as missing.",
                    label, path, max_bytes,
                )
                return None
            payload: object = json.loads(raw)
            return payload
    except (OSError, json.JSONDecodeError, RecursionError, UnicodeDecodeError):
        return None
